#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — deterministic document-library repair engine (stdlib only).

Design goals
------------
1. Read-only on all source material: the engine inventories, hashes, groups,
   links and reconciles, but never moves, overwrites or deletes a source file.
2. Skeleton-first: a legal partial result.json is written before any analysis,
   so an interrupted run never leaves an empty deliverable.
3. Deterministic: traversal is sorted, hashing is stable, no randomness. Two
   runs on the same input differ only in timestamps.
4. Honest degradation: every skip, error and unknown is recorded in
   errors/degradations/limitations/uncertainty/pending_human. Nothing is
   fabricated; missing evidence is marked `not_reported` / `pending_human`.
5. Incremental: `--skeleton-only` / `--update` / `--check` let the Agent save a
   partial deliverable early and improve it without recomputing the world.

Runtime contract
----------------
Input (read-only): /input/case.json, /input/task.md and any material files.
Work dir: /app/work (intermediate inventory.json / library.json).
Output: /app/output/result.json plus editable artifacts (CSV/tree/JSON/MD/log).

All entry paths are stored POSIX-style relative to the input root so results
are portable and re-runnable.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
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

# --------------------------------------------------------------------------
# Constants / defaults
# --------------------------------------------------------------------------

RESERVED_KEYS = frozenset({
    "case_id", "status", "sources", "uncertainty", "change_log", "computed",
    "errors", "degradations", "runtime", "limitations", "pending_human",
})

CONTRACT_KEYS = (
    "output_contract", "output", "result_schema", "contract",
    "output_schema", "response_schema", "return_schema",
)

DEFAULT_MAX_HASH_BYTES = 16 * 1024 * 1024   # content-hash budget per file
DEFAULT_MAX_TEXT_BYTES = 2 * 1024 * 1024    # text-read budget per file
DEFAULT_SIMILARITY_BYTES = 512 * 1024       # diff budget per text file pair

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
ALL_STATUSES = frozenset({STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR})

ISO_UTC = "%Y-%m-%dT%H:%M:%SZ"

CATALOG_EXTS = {".json": "json", ".csv": "csv", ".bib": "bibtex",
                ".ris": "ris", ".db": "sqlite", ".sqlite": "sqlite",
                ".sqlite3": "sqlite"}

CONTROL_BASENAMES = frozenset({
    "case.json", "task.md", "challenge.json", "result.json", "output.json",
})

NON_TEXT_EXTS = frozenset({
    "pdf", "png", "jpg", "jpeg", "gif", "tif", "tiff", "bmp", "webp",
    "zip", "gz", "tgz", "tar", "7z", "rar", "docx", "xlsx", "pptx",
    "doc", "xls", "ppt", "bin", "exe", "dll", "so", "dylib", "dat",
})

VERSION_NOTE_HINTS = (
    "replaces", "supersedes", "replaced by", "superseded by", "version of",
    "previous version", "derived from", "->", "→", "revision",
)


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime(ISO_UTC)


def _abs(input_root: str, rel: str) -> str:
    return os.path.join(input_root, *rel.split("/"))


def _rel(input_root: str, abs_path: str) -> str:
    rel = os.path.relpath(abs_path, input_root)
    return rel.replace(os.sep, "/") if rel != "." else "."


# --------------------------------------------------------------------------
# Robust readers
# --------------------------------------------------------------------------

def read_text_robust(path, max_bytes=DEFAULT_MAX_TEXT_BYTES):
    """Read text with BOM/encoding tolerance; returns (text, error_or_None)."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return "", "unreadable:" + str(exc)
    try:
        if size > max_bytes:
            with open(path, "rb") as fh:
                raw = fh.read(max_bytes)
            return raw.decode("utf-8-sig", errors="replace"), "too_large"
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return "", "unreadable:" + str(exc)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "bad_encoding"


def load_json_robust(path):
    text, err = read_text_robust(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as exc:
        return None, "bad_json:" + str(exc)


def sha256_file(path, max_bytes=DEFAULT_MAX_HASH_BYTES):
    """Streaming SHA-256; returns (hexdigest_or_None, status, size, error)."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "unreadable", None, "unreadable:" + str(exc)
    if size > max_bytes:
        return None, "too_large", size, None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest(), "ok", size, None
    except OSError as exc:
        return None, "unreadable", size, "unreadable:" + str(exc)


# --------------------------------------------------------------------------
# Stat helpers
# --------------------------------------------------------------------------

def _mode_string(st_mode: int) -> str:
    return "%03o" % statmod.S_IMODE(st_mode)


def _mtime_iso(st) -> str:
    try:
        return _dt.datetime.fromtimestamp(st.st_mtime, _dt.timezone.utc).strftime(ISO_UTC)
    except (OSError, OverflowError, ValueError):
        return None


def _link_broken(abs_path: str, target) -> bool:
    if target is None:
        return True
    try:
        resolved = os.path.normpath(os.path.join(os.path.dirname(abs_path), target))
        return not os.path.exists(resolved)
    except OSError:
        return True


def _inspect_file(abs_path, rel, inode_map):
    ent = {
        "path": rel, "type": "file", "size": None, "mtime": None, "mode": None,
        "sha256": None, "hash_status": None, "symlink_target": None,
        "symlink_broken": None, "hardlink_group": None, "extension": None,
        "error": None,
    }
    try:
        lst = os.lstat(abs_path)
    except OSError as exc:
        ent["error"] = "stat:" + str(exc)
        return ent
    ent["mode"] = _mode_string(lst.st_mode)
    ent["mtime"] = _mtime_iso(lst)

    if statmod.S_ISLNK(lst.st_mode):
        ent["type"] = "symlink"
        try:
            tgt = os.readlink(abs_path)
        except OSError as exc:
            ent["error"] = "readlink:" + str(exc)
            tgt = None
        ent["symlink_target"] = tgt
        ent["symlink_broken"] = _link_broken(abs_path, tgt)
        return ent

    if not statmod.S_ISREG(lst.st_mode):
        ent["type"] = "non_regular"
        ent["size"] = lst.st_size
        ent["hash_status"] = "non_regular"
        return ent

    ent["size"] = lst.st_size
    ent["extension"] = os.path.splitext(rel)[1].lstrip(".").lower() or None

    # Hardlink grouping: same (st_dev, st_ino) with nlink > 1, best effort.
    try:
        st = os.stat(abs_path)
        if getattr(st, "st_nlink", 1) > 1:
            key = (getattr(st, "st_dev", 0), getattr(st, "st_ino", 0))
            if key[1]:
                gid = inode_map.get(key)
                if gid is None:
                    gid = len(inode_map) + 1
                    inode_map[key] = gid
                ent["hardlink_group"] = gid
    except OSError:
        pass

    digest, status, _sz, err = sha256_file(abs_path)
    ent["sha256"] = digest
    ent["hash_status"] = status
    if err and digest is None and status != "too_large":
        ent["error"] = err
    return ent


def walk_inventory(input_root):
    """Return (entries, dirs, errors). entries/dirs sorted deterministically."""
    entries, dirs, errors = [], [], []
    root_abs = os.path.abspath(input_root)
    if not os.path.isdir(root_abs):
        return entries, dirs, [{"item": input_root, "error": "input_root_missing"}]
    inode_map = {}
    for dirpath, dirnames, filenames in os.walk(root_abs):
        dirnames.sort()
        filenames.sort()
        rel_dir = _rel(root_abs, dirpath)
        try:
            dst = os.lstat(dirpath)
        except OSError as exc:
            errors.append({"item": rel_dir, "error": "stat:" + str(exc)})
            dst = None
        if dst is not None:
            dirs.append({
                "path": rel_dir if rel_dir != "." else ".",
                "type": "dir", "mode": _mode_string(dst.st_mode),
                "mtime": _mtime_iso(dst),
            })
        for name in filenames:
            abs_path = os.path.join(dirpath, name)
            rel = _rel(root_abs, abs_path)
            ent = _inspect_file(abs_path, rel, inode_map)
            if ent.get("error"):
                errors.append({"item": rel, "error": ent["error"]})
            entries.append(ent)
    entries.sort(key=lambda e: e["path"])
    dirs.sort(key=lambda d: d["path"])
    return entries, dirs, errors


# --------------------------------------------------------------------------
# Normalization / grouping
# --------------------------------------------------------------------------

def normalize_stem(name: str) -> str:
    """Heuristic version-stem normalizer. Lowercase, drop dates, version and
    lifecycle tokens, collapse separators. Marked heuristic in output."""
    base = os.path.splitext(name)[0].lower()
    base = re.sub(r"20\d{2}[-_.]?\d{2}[-_.]?\d{2}", " ", base)
    base = re.sub(r"20\d{6}", " ", base)
    base = re.sub(
        r"(?i)(\b|[-_.])(?:v|ver|version|rev|r)?\d+(?:\.\d+)*(?:[-_.]?(?:final|draft|beta|alpha|rc)\d*)?",
        " ", base)
    base = re.sub(
        r"(?<![a-z0-9])(final|draft|copy|backup|old|new|latest|clean|edited|updated|revised)(?![a-z0-9])",
        " ", base)
    base = re.sub(r"[^a-z0-9]+", " ", base).strip()
    return re.sub(r"\s+", " ", base)


def compute_duplicates(entries):
    by_hash = {}
    for e in entries:
        if e.get("type") == "file" and e.get("sha256"):
            by_hash.setdefault(e["sha256"], []).append(e["path"])
    groups = [
        {"sha256": h, "count": len(v), "files": sorted(v)}
        for h, v in by_hash.items() if len(v) > 1
    ]
    groups.sort(key=lambda g: (-g["count"], g["sha256"]))
    return groups


def _text_similarity(input_root, e1, e2):
    if e1.get("size") is None or e2.get("size") is None:
        return None
    if e1["size"] > DEFAULT_SIMILARITY_BYTES or e2["size"] > DEFAULT_SIMILARITY_BYTES:
        return None
    try:
        t1, _ = read_text_robust(_abs(input_root, e1["path"]), DEFAULT_SIMILARITY_BYTES)
        t2, _ = read_text_robust(_abs(input_root, e2["path"]), DEFAULT_SIMILARITY_BYTES)
    except OSError:
        return None
    if not t1 and not t2:
        return 1.0
    return round(difflib.SequenceMatcher(None, t1, t2).ratio(), 4)


def compute_versions(input_root, entries):
    files = [e for e in entries if e.get("type") == "file" and e.get("sha256")]
    buckets = {}
    for e in files:
        stem = normalize_stem(os.path.basename(e["path"]))
        ext = e.get("extension") or ""
        if not stem:
            continue
        buckets.setdefault((stem, ext), []).append(e)
    candidates = []
    for (stem, ext), members in buckets.items():
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda m: (m.get("mtime") or "", m["path"]))
        hashes = [m["sha256"] for m in members]
        sims = []
        for i in range(len(members) - 1):
            sims.append(_text_similarity(input_root, members[i], members[i + 1]))
        candidates.append({
            "stem": stem, "extension": ext,
            "files": [{"path": m["path"], "sha256": m["sha256"],
                       "mtime": m["mtime"], "size": m["size"]} for m in members],
            "distinct_hashes": len(set(hashes)),
            "adjacent_similarity": sims,
            "heuristic": True,
        })
    candidates.sort(key=lambda c: (-len(c["files"]), c["stem"]))
    return candidates


def compute_aliases(entries):
    symlinks = [e for e in entries if e.get("type") == "symlink"]
    broken = [e["path"] for e in symlinks if e.get("symlink_broken")]
    hard = {}
    for e in entries:
        g = e.get("hardlink_group")
        if g:
            hard.setdefault(g, []).append(e["path"])
    hard_groups = sorted(sorted(v) for v in hard.values() if len(v) > 1)
    return {
        "symlinks": [{"path": e["path"], "target": e["symlink_target"],
                      "broken": bool(e.get("symlink_broken"))}
                     for e in sorted(symlinks, key=lambda x: x["path"])],
        "broken_links": sorted(broken),
        "hardlink_groups": hard_groups,
    }


# --------------------------------------------------------------------------
# Catalog parsing (JSON / CSV / BibTeX / RIS / SQLite)
# --------------------------------------------------------------------------

def _entry_from_mapping(m):
    out = {}
    for key in ("path", "file", "filename", "relpath", "name"):
        if m.get(key):
            out["path"] = str(m[key]).replace("\\", "/")
            break
    for key in ("sha256", "hash", "checksum", "md5", "sha1"):
        if m.get(key):
            out["hash"] = str(m[key]).lower()
            break
    for key in ("version", "ver", "rev"):
        if m.get(key) is not None:
            out["version"] = str(m[key])
            break
    for key in ("author", "authors", "creator"):
        if m.get(key):
            out["author"] = m[key]
            break
    for key in ("year", "date", "pubyear"):
        if m.get(key):
            out["year"] = str(m[key])
            break
    for key in ("title", "description"):
        if m.get(key):
            out["title"] = str(m[key])
            break
    return out


def _parse_json_catalog(data):
    entries = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                entries.append(_entry_from_mapping(item))
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                ent = _entry_from_mapping(v)
                if not ent.get("path") and re.search(r"[./]", str(k)):
                    ent["path"] = str(k).replace("\\", "/")
                entries.append(ent)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        entries.append(_entry_from_mapping(item))
    return [e for e in entries if e.get("path") or e.get("hash")]


def _parse_csv_catalog(text):
    entries = []
    try:
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            if row:
                entries.append(_entry_from_mapping(row))
    except csv.Error:
        return entries
    return [e for e in entries if e.get("path") or e.get("hash")]


def _parse_bibtex(text):
    entries = []
    # Split records on @type{ ... }
    for m in re.finditer(r"@\w+\s*\{([^,]+),((?:[^{}]|\{[^{}]*\})*)\}", text, re.S):
        key = m.group(1).strip()
        body = m.group(2)
        ent = {"key": key}
        for fm in re.finditer(r"(\w+)\s*=\s*[\{]([^{}]*)[\}]", body):
            ent[fm.group(1).lower()] = fm.group(2).strip()
        for fm in re.finditer(r"(\w+)\s*=\s*\"([^\"]*)\"", body):
            ent[fm.group(1).lower()] = fm.group(2).strip()
        out = _entry_from_mapping(ent)
        out.setdefault("title", ent.get("title"))
        if not out.get("author"):
            out["author"] = ent.get("author")
        if not out.get("year"):
            out["year"] = ent.get("year")
        if ent.get("file"):
            out["path"] = str(ent["file"]).replace("\\", "/")
        entries.append(out)
    return entries


def _parse_ris(text):
    entries, cur = [], None
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if len(line) >= 2 and line[0] == "T" and line[1] in ("Y", " "):
            if cur:
                entries.append(cur)
            cur = {}
            continue
        if len(line) >= 6 and line[0] == "E" and line[1] == "R" and line[2] == " ":
            if cur:
                entries.append(cur)
            cur = None
            continue
        if cur is not None and len(line) >= 6 and line[2:4] == "  ":
            tag, value = line[0:2], line[6:].strip()
            cur[tag] = value
    if cur:
        entries.append(cur)
    out = []
    for ent in entries:
        mapped = _entry_from_mapping({
            "path": ent.get("L1") or ent.get("UR") or ent.get("DO"),
            "title": ent.get("TI"),
            "author": ent.get("AU"),
            "year": ent.get("PY"),
        })
        out.append(mapped)
    return [e for e in out if e.get("path") or e.get("hash")]


def _parse_sqlite_catalog(path):
    entries, err = [], None
    try:
        con = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
        try:
            cur = con.cursor()
            tables = [r[0] for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            for t in tables:
                try:
                    rows = cur.execute("SELECT * FROM %s" % t).fetchall()
                    cols = [d[0] for d in cur.description]
                    for row in rows[:1000]:
                        m = dict(zip(cols, row))
                        ent = _entry_from_mapping(m)
                        if ent.get("path") or ent.get("hash"):
                            entries.append(ent)
                except sqlite3.Error as exc:
                    err = "sqlite_query_error:" + str(exc)
        finally:
            con.close()
    except sqlite3.Error as exc:
        err = "sqlite_open_error:" + str(exc)
    return entries, err


def parse_catalog(abs_path, ext):
    """Return (entries, format, error). Never raises."""
    fmt = CATALOG_EXTS.get(ext)
    entries, err = [], None
    if fmt == "json":
        data, jerr = load_json_robust(abs_path)
        if jerr:
            err = jerr
        else:
            entries = _parse_json_catalog(data)
    elif fmt == "csv":
        text, terr = read_text_robust(abs_path)
        if terr:
            err = terr
        else:
            entries = _parse_csv_catalog(text)
    elif fmt == "bibtex":
        text, terr = read_text_robust(abs_path)
        if terr:
            err = terr
        else:
            entries = _parse_bibtex(text)
    elif fmt == "ris":
        text, terr = read_text_robust(abs_path)
        if terr:
            err = terr
        else:
            entries = _parse_ris(text)
    elif fmt == "sqlite":
        entries, err = _parse_sqlite_catalog(abs_path)
    return entries, fmt, err


def scan_version_notes(input_root, entries):
    notes = []
    for e in entries:
        if e.get("type") != "file":
            continue
        if e.get("extension") in NON_TEXT_EXTS:
            continue
        text, err = read_text_robust(_abs(input_root, e["path"]), 1024 * 1024)
        if err or not text:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            if any(h in low for h in VERSION_NOTE_HINTS):
                notes.append({"file": e["path"], "line": lineno,
                              "text": line.strip()[:500]})
    return notes


# --------------------------------------------------------------------------
# Reconciliation / index / answers
# --------------------------------------------------------------------------

def reconcile(expected_entries, actual_entries):
    actual_by_path = {e["path"]: e for e in actual_entries}
    actual_by_hash = {}
    for e in actual_entries:
        if e.get("sha256"):
            actual_by_hash.setdefault(e["sha256"], []).append(e["path"])
    matches, mismatches, missing = [], [], []
    seen_paths = set()
    for exp in expected_entries:
        p = exp.get("path")
        if p:
            seen_paths.add(p)
        act = actual_by_path.get(p) if p else None
        if act is None and exp.get("hash"):
            cands = actual_by_hash.get(exp["hash"], [])
            if len(cands) == 1:
                matches.append({"expected": exp, "actual": cands[0], "match": "by_hash"})
                seen_paths.add(cands[0])
                continue
        if act is None:
            missing.append(exp)
        elif exp.get("hash") and act.get("sha256") and exp["hash"] != act["sha256"]:
            mismatches.append({
                "expected": exp, "actual": act["path"],
                "expected_hash": exp["hash"], "actual_hash": act["sha256"],
            })
        else:
            matches.append({"expected": exp, "actual": act["path"], "match": "path"})
    orphaned = sorted(
        e["path"] for e in actual_entries
        if e.get("type") == "file" and e["path"] not in seen_paths)
    return {
        "matches": matches, "mismatches": mismatches,
        "missing": missing, "orphaned": orphaned,
    }


def _tokenize(text):
    return [t for t in re.findall(r"[a-z0-9]{2,}", text.lower())]


def build_index(entries, catalogs):
    index = {"by_path": {}, "by_hash": {}, "by_extension": {}, "by_token": {}}
    for e in entries:
        index["by_path"][e["path"]] = e
        if e.get("sha256"):
            index["by_hash"].setdefault(e["sha256"], []).append(e["path"])
        ext = e.get("extension") or "(none)"
        index["by_extension"].setdefault(ext, []).append(e["path"])
        for t in _tokenize(e["path"]):
            index["by_token"].setdefault(t, []).append(e["path"])
    return index


def make_answers(entries, dirs, dups, versions, aliases, recon, catalogs, version_notes):
    files = [e for e in entries if e.get("type") == "file"]
    syms = [e for e in entries if e.get("type") == "symlink"]
    total_size = sum(e["size"] for e in files if isinstance(e.get("size"), int))
    hashed = sum(1 for e in files if e.get("sha256"))
    answers = []

    def add(question, answer, locator, quote):
        answers.append({"question": question, "answer": answer,
                        "locator": locator, "quote": quote})

    add("file_count", len(files), "inventory:entries",
        "%d regular files inventoried" % len(files))
    add("directory_count", len(dirs), "inventory:dirs",
        "%d directories inventoried" % len(dirs))
    add("symlink_count", len(syms), "inventory:symlinks",
        "%d symlinks inventoried" % len(syms))
    add("total_size_bytes", total_size, "inventory:entries",
        "sum of reported regular-file sizes")
    add("hashed_files", hashed, "inventory:entries",
        "%d of %d files have a content SHA-256" % (hashed, len(files)))
    if dups:
        add("duplicate_groups", len(dups), "dedup",
            "content-SHA-256 duplicate groups (first 8 chars per group)")
    if versions:
        add("version_candidate_groups", len(versions), "versions",
            "heuristic version-candidate chains by normalized stem")
    if aliases["broken_links"]:
        add("broken_symlinks", aliases["broken_links"], "aliases",
            "symlinks whose targets do not resolve")
    if recon["missing"]:
        add("reconcile_missing", [m for m in recon["missing"]], "reconciliation",
            "expected entries not found in the actual tree")
    if recon["mismatches"]:
        add("reconcile_mismatches", [m for m in recon["mismatches"]], "reconciliation",
            "expected entries found but content hash differs")
    if catalogs:
        add("catalogs_parsed", [c["path"] for c in catalogs], "catalogs",
            "library catalogs parsed for reconciliation")
    return answers


# --------------------------------------------------------------------------
# Artifact rendering
# --------------------------------------------------------------------------

def render_tree(entries, dirs):
    children = {}
    dir_set = set(d["path"] for d in dirs if d["path"] != ".")
    file_types = {e["path"]: e for e in entries}

    def ensure_parents(p):
        parts = p.split("/")
        for i in range(1, len(parts)):
            dir_set.add("/".join(parts[:i]))

    for p in list(dir_set):
        ensure_parents(p)
    for d in sorted(dir_set):
        if not d:
            continue
        parent = d.rsplit("/", 1)[0] if "/" in d else ""
        children.setdefault(parent, {"dirs": [], "files": []})["dirs"].append(d)
    for fp in sorted(file_types):
        parent = fp.rsplit("/", 1)[0] if "/" in fp else ""
        children.setdefault(parent, {"dirs": [], "files": []})["files"].append(fp)

    def is_empty(d):
        node = children.get(d)
        return (node is None) or (not node["dirs"] and not node["files"])

    lines = []

    def walk(node, prefix):
        node_children = children.get(node, {"dirs": [], "files": []})
        dirs_here = sorted(node_children["dirs"], key=lambda d: d.rsplit("/", 1)[-1].lower())
        files_here = sorted(node_children["files"], key=lambda f: f.rsplit("/", 1)[-1].lower())
        items = [("dir", d.rsplit("/", 1)[-1], d) for d in dirs_here]
        items += [("file", f.rsplit("/", 1)[-1], f) for f in files_here]
        for i, (kind, name, path) in enumerate(items):
            last = i == len(items) - 1
            conn = "└── " if last else "├── "
            if kind == "dir":
                tag = " [空目录]" if is_empty(path) else ""
                lines.append(prefix + conn + name + "/" + tag)
                walk(path, prefix + ("    " if last else "│   "))
            else:
                e = file_types[path]
                meta = ""
                if e.get("type") != "file":
                    meta = " [" + e["type"] + "]"
                elif e.get("sha256"):
                    meta = "  sha256=" + e["sha256"][:8]
                lines.append(prefix + conn + name + meta)

    lines.append("/")
    walk("", "")
    return "\n".join(lines)


def build_manifest_rows(entries):
    rows = []
    for e in entries:
        rows.append({
            "path": e["path"], "type": e["type"], "size": e.get("size"),
            "mtime": e.get("mtime"), "mode": e.get("mode"),
            "sha256": e.get("sha256"), "hash_status": e.get("hash_status"),
            "extension": e.get("extension"), "symlink_target": e.get("symlink_target"),
            "symlink_broken": "" if e.get("symlink_broken") is None else str(e["symlink_broken"]).lower(),
            "hardlink_group": e.get("hardlink_group"),
        })
    return rows


# --------------------------------------------------------------------------
# Result assembly
# --------------------------------------------------------------------------

def _placeholder(spec):
    if isinstance(spec, str):
        return [] if re.search(r"\b(list|array)\b", spec, re.I) else None
    if isinstance(spec, dict):
        return [] if str(spec.get("type", "")).lower() in ("list", "array") else None
    if isinstance(spec, (list, tuple)):
        return []
    return None


def apply_contract(result, contract):
    """Echo all output_contract fields. Reserved-key conflicts go to
    computed._contract_<field>; missing values become null/[]."""
    if not isinstance(contract, dict):
        return
    for key, spec in contract.items():
        if not isinstance(key, str):
            continue
        if key in RESERVED_KEYS:
            result.setdefault("computed", {})["_contract_" + key] = _placeholder(spec)
        else:
            result[key] = _placeholder(spec)


def load_contract(case_json_path):
    if not case_json_path or not os.path.exists(case_json_path):
        return None
    data, err = load_json_robust(case_json_path)
    if err or not isinstance(data, dict):
        return None
    for key in CONTRACT_KEYS:
        if isinstance(data.get(key), dict):
            return data[key]
    return None


def detect_case_id(case_json_path, task_text):
    if case_json_path and os.path.exists(case_json_path):
        data, err = load_json_robust(case_json_path)
        if not err and isinstance(data, dict):
            for key in ("case_id", "id", "challenge_id", "challengeId"):
                if data.get(key):
                    return str(data[key])
    for pat in (r"(?i)challenge[-_]?id\s*[:=]\s*['\"]?([A-Za-z0-9_-]+)",
                r"(?i)case[-_]?id\s*[:=]\s*['\"]?([A-Za-z0-9_-]+)"):
        m = re.search(pat, task_text or "")
        if m:
            return m.group(1)
    return ""


def build_skeleton(case_id, contract, status=STATUS_PARTIAL, errors=None):
    result = {
        "case_id": case_id,
        "status": status,
        "computed": {},
        "sources": [],
        "uncertainty": [],
        "change_log": [{
            "time": now_iso(), "actor": "scripts/core_impl.py",
            "step": "skeleton", "detail": "partial skeleton written before analysis",
        }],
        "errors": list(errors or []),
        "degradations": [],
        "runtime": {"started": now_iso(), "finished": None, "seconds": None},
        "limitations": [],
        "pending_human": [],
    }
    apply_contract(result, contract)
    return result


def write_json_atomic(path, obj):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def deep_merge(base, patch):
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


# --------------------------------------------------------------------------
# Full pipeline
# --------------------------------------------------------------------------

def run_full(case_json_path, task_path, input_root, output_dir, work_dir,
             max_hash_bytes=DEFAULT_MAX_HASH_BYTES, skeleton_only=False):
    started = time.time()
    op_log = []
    degradations = []
    limitations = []
    uncertainties = []
    errors = []

    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    task_text = ""
    if task_path and os.path.exists(task_path):
        task_text, terr = read_text_robust(task_path, 1024 * 1024)
        if terr:
            degradations.append({"item": "task.md", "issue": terr})

    case_id = detect_case_id(case_json_path, task_text)
    contract = load_contract(case_json_path)
    if case_json_path and os.path.exists(case_json_path):
        _, jerr = load_json_robust(case_json_path)
        if jerr:
            errors.append({"item": "case.json", "error": jerr})
            degradations.append({"item": "case.json", "issue": jerr})
    if not case_json_path or not os.path.exists(case_json_path):
        errors.append({"item": "case.json", "error": "missing"})
        status_final = STATUS_INPUT_ERROR
    elif not case_id:
        errors.append({"item": "case.json", "error": "case_id not detected"})
        status_final = STATUS_PARTIAL
    else:
        status_final = STATUS_OK

    if contract is None:
        degradations.append({
            "item": "case.json", "issue": "output_contract missing; only metadata fields emitted",
        })

    result = build_skeleton(case_id, contract, status=STATUS_PARTIAL, errors=errors)
    result_path = os.path.join(output_dir, "result.json")
    write_json_atomic(result_path, result)

    if skeleton_only:
        _finish_runtime(result, started)
        write_json_atomic(result_path, result)
        op_log.append({"time": now_iso(), "actor": "scripts/core_impl.py",
                       "step": "skeleton", "detail": "skeleton-only run complete"})
        _write_op_log(os.path.join(output_dir, "operation_log.jsonl"), op_log)
        return result

    # --- inventory ---
    entries, dirs, inv_errors = walk_inventory(input_root)
    errors.extend(inv_errors)
    too_large = [e["path"] for e in entries if e.get("hash_status") == "too_large"]
    unreadable = [e["path"] for e in entries if e.get("error") and e.get("hash_status") in (None, "unreadable")]
    if too_large:
        degradations.append({"item": "inventory", "issue": "too_large",
                             "files": too_large,
                             "note": "content hash skipped for files above size budget"})
    if unreadable:
        degradations.append({"item": "inventory", "issue": "unreadable",
                             "files": unreadable})
    material_files = [
        e for e in entries
        if e.get("type") == "file"
        and os.path.basename(e["path"]).lower() not in CONTROL_BASENAMES
    ]
    material_dirs = [d for d in dirs if d["path"] != "."]
    if not material_files and not material_dirs and status_final != STATUS_INPUT_ERROR:
        status_final = STATUS_INSUFFICIENT
        limitations.append(
            "no material files or directories beyond control files "
            "(case.json/task.md) under the input root")

    if status_final == STATUS_OK and errors:
        status_final = STATUS_PARTIAL
    if status_final == STATUS_OK and degradations:
        status_final = STATUS_PARTIAL

    # --- grouping ---
    dups = compute_duplicates(entries)
    versions = compute_versions(input_root, entries)
    aliases = compute_aliases(entries)
    version_notes = scan_version_notes(input_root, entries)

    # --- catalogs / reconciliation ---
    catalogs = []
    expected_entries = []
    for e in entries:
        if e.get("type") != "file":
            continue
        ext = e.get("extension")
        if not ext:
            continue
        if os.path.basename(e["path"]).lower() in CONTROL_BASENAMES:
            continue
        key = "." + ext
        if key in CATALOG_EXTS:
            abs_path = _abs(input_root, e["path"])
            cat_entries, fmt, cerr = parse_catalog(abs_path, key)
            rec = {"path": e["path"], "format": fmt, "entry_count": len(cat_entries)}
            if cerr:
                rec["error"] = cerr
                degradations.append({"item": e["path"], "issue": cerr})
            catalogs.append(rec)
            expected_entries.extend(cat_entries)

    recon = reconcile(expected_entries, entries)
    index = build_index(entries, catalogs)
    answers = make_answers(entries, dirs, dups, versions, aliases, recon,
                           catalogs, version_notes)

    # --- evidence sources for key conclusions ---
    sources = []
    for g in dups:
        sources.append({
            "item": "duplicate_group", "locator": "; ".join(g["files"]),
            "quote": "sha256=" + g["sha256"],
        })
    for v in versions:
        loc = "; ".join(f["path"] for f in v["files"])
        sources.append({
            "item": "version_candidate", "locator": loc,
            "quote": "normalized_stem=" + v["stem"] + " (heuristic)",
        })
    for s in aliases["symlinks"]:
        sources.append({
            "item": "symlink", "locator": s["path"],
            "quote": "-> " + str(s["target"]) + (" (broken)" if s["broken"] else ""),
        })
    for g in aliases["hardlink_groups"]:
        sources.append({
            "item": "hardlink_group", "locator": "; ".join(g),
            "quote": "same (st_dev, st_ino)",
        })
    for n in version_notes:
        sources.append({
            "item": "version_note", "locator": "%s:%d" % (n["file"], n["line"]),
            "quote": n["text"],
        })
    for m in recon["mismatches"]:
        sources.append({
            "item": "reconcile_mismatch", "locator": m["actual"],
            "quote": "expected_hash=%s actual_hash=%s" % (m["expected_hash"], m["actual_hash"]),
        })
    for miss in recon["missing"]:
        sources.append({
            "item": "reconcile_missing", "locator": str(miss),
            "quote": "expected entry not found in actual tree",
        })

    if dups:
        uncertainties.append({
            "item": "dedup", "severity": "low", "reason":
            "duplicate groups are content-based (SHA-256); files skipped as "
            "too_large/unreadable are excluded, so the group set is complete "
            "only for hashed files.",
        })
    if versions:
        uncertainties.append({
            "item": "version_candidates", "severity": "medium", "reason":
            "version chains are heuristic: grouping by normalized filename stem "
            "and mtime order can merge unrelated files or split real chains; "
            "semantic confirmation is required before treating them as facts.",
        })
    if not contract:
        uncertainties.append({
            "item": "output_contract", "severity": "low", "reason":
            "no output_contract found; result only carries default metadata fields.",
        })

    # --- assemble result ---
    result.update({
        "case_id": case_id,
        "status": status_final,
        "computed": {
            "files": entries,
            "summary": {
                "file_count": sum(1 for e in entries if e.get("type") == "file"),
                "dir_count": len(dirs),
                "symlink_count": sum(1 for e in entries if e.get("type") == "symlink"),
                "duplicate_groups": len(dups),
                "version_candidate_groups": len(versions),
                "catalog_files": len(catalogs),
                "reconcile_matches": len(recon["matches"]),
                "reconcile_mismatches": len(recon["mismatches"]),
                "reconcile_missing": len(recon["missing"]),
                "reconcile_orphaned": len(recon["orphaned"]),
            },
            "inventory": {"input_root": os.path.abspath(input_root)},
            "duplicate_groups": dups,
            "version_candidates": versions,
            "version_notes": version_notes,
            "aliases": aliases,
            "reconciliation": recon,
            "answers": answers,
            "index": index,
            "artifacts": [
                "library_manifest.csv", "directory_tree.txt",
                "dedup_version.json", "recovery_report.json",
                "library_index.json", "library_manifest.md",
                "operation_log.jsonl",
            ],
        },
        "sources": sources,
        "uncertainty": uncertainties,
        "errors": errors,
        "degradations": degradations,
        "limitations": limitations,
        "pending_human": [],
    })
    apply_contract(result, contract)

    # --- write artifacts ---
    _write_artifacts(output_dir, entries, dirs, dups, versions, version_notes,
                     aliases, recon, catalogs, index, answers, result)

    _finish_runtime(result, started)
    op_log.append({"time": now_iso(), "actor": "scripts/core_impl.py",
                   "step": "finalize",
                   "detail": "status=%s files=%d dup_groups=%d version_groups=%d"
                             % (status_final, result["computed"]["summary"]["file_count"],
                                len(dups), len(versions))})
    write_json_atomic(result_path, result)
    _write_op_log(os.path.join(output_dir, "operation_log.jsonl"), op_log)
    return result


def _finish_runtime(result, started):
    result["runtime"]["finished"] = now_iso()
    result["runtime"]["seconds"] = round(time.time() - started, 3)
    result["change_log"].append({
        "time": now_iso(), "actor": "scripts/core_impl.py",
        "step": "runtime", "detail": "run completed",
    })


def _write_op_log(path, op_log):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for entry in op_log:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _write_artifacts(output_dir, entries, dirs, dups, versions, version_notes,
                     aliases, recon, catalogs, index, answers, result):
    # CSV manifest
    rows = build_manifest_rows(entries)
    fieldnames = ["path", "type", "size", "mtime", "mode", "sha256",
                  "hash_status", "extension", "symlink_target", "symlink_broken",
                  "hardlink_group"]
    with open(os.path.join(output_dir, "library_manifest.csv"), "w",
              newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # Directory tree
    with open(os.path.join(output_dir, "directory_tree.txt"), "w",
              encoding="utf-8") as fh:
        fh.write(render_tree(entries, dirs) + "\n")

    # dedup / version / alias / notes
    dv = {
        "duplicate_groups": dups,
        "version_candidates": versions,
        "version_notes": version_notes,
        "aliases": aliases,
        "heuristic_note": (
            "version_candidates group by normalized filename stem (dates, version "
            "tokens and lifecycle words removed) ordered by mtime; treat as "
            "hypotheses, not facts."),
    }
    write_json_atomic(os.path.join(output_dir, "dedup_version.json"), dv)

    # recovery report
    rr = {
        "reconciliation": recon,
        "catalogs": catalogs,
        "catalog_formats": [c["format"] for c in catalogs],
    }
    write_json_atomic(os.path.join(output_dir, "recovery_report.json"), rr)

    # searchable index (trim heavy by_path values to keep file small)
    idx = {
        "by_hash": index["by_hash"],
        "by_extension": index["by_extension"],
        "by_token": index["by_token"],
        "paths": sorted(index["by_path"]),
    }
    write_json_atomic(os.path.join(output_dir, "library_index.json"), idx)

    # handover markdown report
    with open(os.path.join(output_dir, "library_manifest.md"), "w",
              encoding="utf-8") as fh:
        fh.write(_render_handover(result, catalogs))


def _render_handover(result, catalogs):
    s = result["computed"]["summary"]
    lines = [
        "# 资料库交接报告（library handover）",
        "",
        "- case_id: `%s`" % result.get("case_id"),
        "- status: `%s`" % result.get("status"),
        "- 文件数: %d | 目录数: %d | 符号链接: %d" % (s["file_count"], s["dir_count"], s["symlink_count"]),
        "- 重复组（按内容 SHA-256）: %d" % s["duplicate_groups"],
        "- 版本候选链（启发式）: %d" % s["version_candidate_groups"],
        "- 对账: matches=%d mismatches=%d missing=%d orphaned=%d" % (
            s["reconcile_matches"], s["reconcile_mismatches"],
            s["reconcile_missing"], s["reconcile_orphaned"]),
        "",
        "## 产物",
        "",
        "| 文件 | 内容 |",
        "|---|---|",
        "| `library_manifest.csv` | 每行一个源文件：路径/类型/大小/权限/修改时间/哈希/别名 |",
        "| `directory_tree.txt` | 目录树，空目录标 `[空目录]` |",
        "| `dedup_version.json` | 重复组、版本候选、版本说明、别名（含断链/硬链接） |",
        "| `recovery_report.json` | 恢复对账 matches/mismatches/missing/orphaned |",
        "| `library_index.json` | 可检索索引（路径/哈希/扩展名/词项） |",
        "| `operation_log.jsonl` | 操作时间线 |",
        "",
        "## 证据与不确定性",
        "",
        "- 每个关键结论在 `result.json` 的 `sources` 中有 locator + 逐字 quote。",
        "- 版本链是启发式候选，需人工确认；详见 `result.json` 的 `uncertainty`。",
        "- 无法哈希/不可读/超大文件已记录在 `degradations`，未静默跳过。",
        "",
    ]
    if catalogs:
        lines.append("## 已解析的库目录")
        lines.append("")
        for c in catalogs:
            extra = "（%s）" % c["error"] if c.get("error") else ""
            lines.append("- `%s`：%s，%d 条%s" % (c["path"], c["format"], c["entry_count"], extra))
        lines.append("")
    if result.get("limitations"):
        lines.append("## 限制")
        lines.append("")
        for lim in result["limitations"]:
            lines.append("- %s" % lim)
        lines.append("")
    if result.get("pending_human"):
        lines.append("## 待人工确认")
        lines.append("")
        for ph in result["pending_human"]:
            lines.append("- %s" % ph)
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Update / check
# --------------------------------------------------------------------------

def run_update(patch_path, result_path):
    if not os.path.exists(result_path):
        raise SystemExit("result.json not found: %s" % result_path)
    patch, perr = load_json_robust(patch_path)
    if perr:
        raise SystemExit("patch is not valid JSON: %s" % perr)
    if not isinstance(patch, dict):
        raise SystemExit("patch must be a JSON object")
    with open(result_path, "r", encoding="utf-8") as fh:
        base = json.load(fh)
    base = deep_merge(base, patch)
    base.setdefault("change_log", []).append({
        "time": now_iso(), "actor": "scripts/core_impl.py",
        "step": "update", "detail": "merged patch from %s" % os.path.basename(patch_path),
    })
    write_json_atomic(result_path, base)
    return base


def check_result(result, case_json_path=None):
    issues = []
    if not isinstance(result, dict):
        return ["result is not a JSON object"]
    if not result.get("case_id"):
        issues.append("case_id is empty")
    if result.get("status") not in ALL_STATUSES:
        issues.append("invalid status: %r" % result.get("status"))
    for key in ("sources", "uncertainty", "change_log", "errors", "degradations",
                "limitations", "pending_human"):
        if key not in result:
            issues.append("missing key: %s" % key)
    for s in result.get("sources", []):
        if not isinstance(s, dict) or not s.get("item") or not s.get("locator") or not s.get("quote"):
            issues.append("bad source entry: %s" % str(s)[:80])
            break
    contract = load_contract(case_json_path)
    if isinstance(contract, dict):
        for key in contract:
            if key in RESERVED_KEYS:
                continue
            if key not in result:
                issues.append("contract field missing from result: %s" % key)
    return issues


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="scripts/main.py",
        description="Deterministic document-library repair engine (stdlib only).")
    p.add_argument("--case-json", default=None)
    p.add_argument("--task-path", default=None)
    p.add_argument("--input-root", default="/input")
    p.add_argument("--output-dir", default="/app/output")
    p.add_argument("--work-dir", default="/app/work")
    p.add_argument("--max-hash-bytes", type=int, default=DEFAULT_MAX_HASH_BYTES)
    p.add_argument("--skeleton-only", action="store_true")
    p.add_argument("--update", metavar="PATCH.json", default=None)
    p.add_argument("--result", default=None)
    p.add_argument("--check", metavar="RESULT.json", default=None)
    p.add_argument("--check-case-json", default=None)
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    if args.check:
        if not os.path.exists(args.check):
            print("check: result file not found: %s" % args.check)
            return 1
        with open(args.check, "r", encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except ValueError as exc:
                print("check: result is not valid JSON: %s" % exc)
                return 1
        issues = check_result(data, args.check_case_json)
        if issues:
            print("check: FAIL (%d issue(s))" % len(issues))
            for i in issues:
                print("  - %s" % i)
            return 1
        print("check: OK")
        return 0

    if args.update:
        result_path = args.result or os.path.join(args.output_dir, "result.json")
        run_update(args.update, result_path)
        print("update: merged into %s" % result_path)
        return 0

    run_full(args.case_json, args.task_path, args.input_root, args.output_dir,
             args.work_dir, max_hash_bytes=args.max_hash_bytes,
             skeleton_only=args.skeleton_only)
    if args.skeleton_only:
        print("finalize: skeleton written -> %s/result.json" % args.output_dir)
    else:
        print("finalize: done -> %s/result.json" % args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
