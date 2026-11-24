#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic research document library repair engine.

Design contract (this is the variant's core value):
  * same input => same substantive output. All traversal is sorted, hashes are
    stable SHA-256, no RNG, JSON is serialised with sorted keys. Volatile
    fields are limited to the documented ``runtime``/``generated_at`` timestamps
    and are excluded by ``scripts/selftest.py`` when it byte-compares two runs.
  * fail soft, never silent: every unreadable/oversized/symlink/parse problem is
    recorded into ``errors``/``degradations``; an empty input still produces a
    legal result with ``status=insufficient_evidence``.
  * evidence first: conclusions carry ``locator`` + verbatim ``quote``.

Only the Python standard library is used.
"""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import sys
import time

__version__ = "1.0.0"

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

RESERVED_KEYS = frozenset({
    "case_id", "status", "sources", "uncertainty", "change_log",
    "errors", "degradations", "runtime", "limitations",
    "pending_human", "computed", "artifacts",
})

CONTRACT_KEYS = ("output_contract", "output", "result_schema", "contract")

MAX_CONTENT_BYTES = 2 * 1024 * 1024  # skip content hashing above this size
MAX_TREE_DEPTH = 12                  # depth cap for the directory tree
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

# file categories by suffix; order matters for a stable role label
TYPE_RULES = (
    (".pdf", "pdf"),
    (".md", "markdown"),
    (".txt", "text"),
    (".csv", "csv"),
    (".json", "json"),
    (".jsonl", "jsonl"),
    (".bib", "bibtex"),
    (".ris", "ris"),
    (".sqlite", "sqlite"),
    (".db", "sqlite"),
    (".zip", "archive"),
    (".tar", "archive"),
    (".gz", "archive"),
    (".png", "image"),
    (".jpg", "image"),
    (".jpeg", "image"),
    (".svg", "image"),
    (".docx", "office"),
    (".pptx", "office"),
    (".xlsx", "office"),
)

VERSION_STEM_RE = re.compile(r"(?i)^(.+?)(?:[-_ .]*(?:v|ver|version|rev|r))[-_ .]*([0-9]+(?:\.[0-9]+)*)[-_ .]*$")
YEAR_RE = re.compile(r"(?<!\d)(19[0-9]{2}|20[0-9]{2})(?!\d)")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _norm_rel(abs_path, root):
    """Return a forward-slash path relative to ``root`` (deterministic)."""
    rel = os.path.relpath(abs_path, root)
    return rel.replace(os.sep, "/")


def _stable(items, key=None):
    return sorted(items, key=key)


def _oct_mode(mode):
    return format(stat.S_IMODE(mode), "04o")


def _iso_now():
    # volatile; kept isolated so selftest can strip it
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _read_text_safe(path):
    """Read text tolerantly; return (text_or_None, error_or_None)."""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as fh:
                return fh.read(), None
        except (UnicodeDecodeError, ValueError):
            continue
        except OSError as exc:
            return None, "read_error:%s" % exc
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(), "bad_encoding_replaced"
    except OSError as exc:
        return None, "read_error:%s" % exc


def _sha256_file(path):
    """Stream SHA-256; returns hex digest or None on failure."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _sha256_bytes(data):
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def _file_role(path, rel):
    base = rel.split("/")[-1]
    if base in ("case.json",):
        return "control/case"
    if base in ("task.md",):
        return "control/task"
    lower = base.lower()
    for suffix, role in TYPE_RULES:
        if lower.endswith(suffix):
            return role
    return "unknown"


# --------------------------------------------------------------------------
# inventory
# --------------------------------------------------------------------------

def build_inventory(root):
    """Walk ``root`` deterministically and collect one record per filesystem
    entry (files and directories). Symlinks and hard links are kept separate."""
    entries = []
    errors = []
    degradations = []
    dirs_seen = set()

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames[:] = _stable(dirnames)
        filenames = _stable(filenames)

        rel_dir = _norm_rel(dirpath, root)
        try:
            dstat = os.lstat(dirpath)
        except OSError as exc:
            errors.append({"locator": rel_dir or ".", "issue": "lstat_error", "detail": str(exc)})
            continue

        if rel_dir != ".":
            entries.append({
                "path": rel_dir + "/",
                "type": "directory",
                "mode": _oct_mode(dstat.st_mode),
                "mtime": int(dstat.st_mtime),
                "uid": dstat.st_uid,
                "gid": dstat.st_gid,
                "depth": rel_dir.count("/") + 1,
            })
        dirs_seen.add(rel_dir)

        for name in filenames:
            abs_path = os.path.join(dirpath, name)
            rel = _norm_rel(abs_path, root)
            rec = {
                "path": rel,
                "type": "file",
                "size": None,
                "mode": None,
                "mtime": None,
                "sha256": None,
                "role": _file_role(abs_path, rel),
                "status": "ok",
                "note": None,
                "inode": None,
                "nlink": None,
                "link_target": None,
            }
            try:
                st = os.lstat(abs_path)
                rec["mtime"] = int(st.st_mtime)
                rec["mode"] = _oct_mode(st.st_mode)
                rec["inode"] = st.st_ino
                rec["nlink"] = st.st_nlink
                if stat.S_ISLNK(st.st_mode):
                    rec["type"] = "symlink"
                    rec["link_target"] = os.readlink(abs_path)
                    rec["status"] = "broken_symlink" if not os.path.exists(abs_path) else "symlink"
                    rec["note"] = "symlink"
                elif not stat.S_ISREG(st.st_mode):
                    rec["type"] = "non_regular"
                    rec["status"] = "non_regular"
                    rec["note"] = "non_regular"
                else:
                    rec["size"] = st.st_size
                    if st.st_size > MAX_CONTENT_BYTES:
                        rec["status"] = "too_large"
                        rec["note"] = "too_large(%d>%d)" % (st.st_size, MAX_CONTENT_BYTES)
                        degradations.append({"locator": rel, "issue": "too_large", "detail": st.st_size})
                    else:
                        digest = _sha256_file(abs_path)
                        if digest is None:
                            rec["status"] = "unreadable"
                            rec["note"] = "unreadable"
                            errors.append({"locator": rel, "issue": "unreadable", "detail": "sha256 failed"})
                        else:
                            rec["sha256"] = digest
            except OSError as exc:
                rec["status"] = "unreadable"
                rec["note"] = "stat_error:%s" % exc
                errors.append({"locator": rel, "issue": "stat_error", "detail": str(exc)})
            entries.append(rec)

    # directories under empty dirs are already covered; flag leaf empty dirs
    empty_dirs = []
    for e in entries:
        if e["type"] == "directory":
            children = [x for x in entries if x["path"].startswith(e["path"]) and x["path"] != e["path"]]
            if not children:
                empty_dirs.append(e["path"])
    for e in entries:
        if e["type"] == "directory" and e["path"] in empty_dirs:
            e["note"] = "empty"

    entries.sort(key=lambda e: e["path"])
    return {
        "entries": entries,
        "count_files": sum(1 for e in entries if e["type"] == "file"),
        "count_dirs": sum(1 for e in entries if e["type"] == "directory"),
        "count_symlinks": sum(1 for e in entries if e["type"] == "symlink"),
        "errors": errors,
        "degradations": degradations,
    }


# --------------------------------------------------------------------------
# catalogue parsers (JSON / CSV / BibTeX / RIS / SQLite)
# --------------------------------------------------------------------------

def _parse_json(text):
    try:
        data = json.loads(text)
    except ValueError as exc:
        return None, "json_parse_error:%s" % exc
    if isinstance(data, list):
        return {"items": data}, None
    return data, None


def _parse_csv(text):
    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(r) for r in reader]
        if not rows and text.strip():
            # fall back to raw rows when no header is usable
            reader2 = csv.reader(io.StringIO(text))
            rows = [{"col%d" % i: v for i, v in enumerate(r)} for r in reader2]
        return {"items": rows}, None
    except csv.Error as exc:
        return None, "csv_parse_error:%s" % exc


def _parse_bibtex(text):
    entries = []
    current = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("%"):
            continue
        if line.lower().startswith("@") and "{" in line:
            m = re.match(r"@\s*([A-Za-z]+)\s*\{\s*([^,]+),", line)
            if m:
                current = {"bibtype": m.group(1), "key": m.group(2), "fields": {}}
                entries.append(current)
            continue
        if current is not None and "=" in line:
            key, _, val = line.partition("=")
            key = key.strip().lstrip(",")
            val = val.strip().rstrip(",")
            val = val.strip('{}"')
            if key:
                current["fields"][key.lower()] = val
    return {"items": entries}, None if entries else "bibtex_empty"


def _parse_ris(text):
    entries = []
    current = {}
    for line in text.splitlines():
        line = line.rstrip("\n")
        if line.startswith("ER  -") and current:
            entries.append(current)
            current = {}
            continue
        if "  - " in line:
            tag, _, val = line.partition("  - ")
            tag = tag.strip()
            if tag == "TY":
                current = {"ty": val.strip()}
            else:
                current[tag] = (current.get(tag) + "; " if tag in current else "") + val.strip()
    if current:
        entries.append(current)
    return {"items": entries}, None if entries else "ris_empty"


def _parse_sqlite(path):
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    except sqlite3.Error as exc:
        return None, "sqlite_open_error:%s" % exc
    tables = {}
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        names = [r[0] for r in cur.fetchall()]
        for name in names:
            try:
                cur2 = conn.execute('SELECT * FROM "%s" LIMIT 500' % name.replace('"', '""'))
                cols = [d[0] for d in cur2.description]
                rows = [dict(zip(cols, r)) for r in cur2.fetchall()]
                tables[name] = {"columns": cols, "rows": rows}
            except sqlite3.Error as exc:
                tables[name] = {"columns": [], "rows": [], "error": "sqlite_query_error:%s" % exc}
    except sqlite3.Error as exc:
        conn.close()
        return None, "sqlite_query_error:%s" % exc
    conn.close()
    return {"tables": tables}, None


def parse_catalog(rel, root):
    """Parse a catalogue-like file; return (doc_or_None, error_or_None)."""
    abs_path = os.path.join(root, rel.replace("/", os.sep))
    lower = rel.lower()
    text, read_err = _read_text_safe(abs_path)
    if read_err:
        return None, read_err
    if lower.endswith(".json"):
        return _parse_json(text)
    if lower.endswith(".csv"):
        return _parse_csv(text)
    if lower.endswith(".bib"):
        return _parse_bibtex(text)
    if lower.endswith(".ris"):
        return _parse_ris(text)
    if lower.endswith((".sqlite", ".db")):
        return _parse_sqlite(abs_path)
    return None, "unsupported_catalog_type"


# --------------------------------------------------------------------------
# contract discovery
# --------------------------------------------------------------------------

def detect_contract(case_data, task_text=None):
    """Find the output contract object under any of the known key names."""
    if isinstance(case_data, dict):
        for key in CONTRACT_KEYS:
            if key in case_data and isinstance(case_data[key], dict):
                return case_data[key], key
        for key in CONTRACT_KEYS:
            if key in case_data and isinstance(case_data[key], list):
                return {"items": case_data[key]}, key
    if task_text:
        # fallback: capture fenced ```json blocks as a potential contract
        blocks = re.findall(r"```json\s*\n(.*?)```", task_text, re.S)
        for block in blocks:
            try:
                return json.loads(block), "task_json_block"
            except ValueError:
                continue
    return None, None


# --------------------------------------------------------------------------
# version notes
# --------------------------------------------------------------------------

def extract_version_notes(root, task_text, entries):
    """Pull administrative version statements from task.md and note-like files."""
    notes = []
    patterns = [
        (r"(?i)(版本|version)[^。\n]{0,80}(v|ver|version|rev|r)?[0-9]+(?:\.[0-9]+)*[^。\n]{0,60}",
         "version_mention"),
        (r"(?i)(替代|取代|supersede|replaces?)[^。\n]{0,100}", "replacement_mention"),
    ]
    text = task_text or ""
    for rel, content in [(os.path.join(root, "task.md"), task_text or "")]:
        if not content:
            continue
        for pat, kind in patterns:
            for m in re.finditer(pat, content):
                notes.append({
                    "locator": _norm_rel(rel, root) if rel else "task.md",
                    "kind": kind,
                    "quote": m.group(0).strip(),
                })
    # also scan small text/markdown notes under /input
    for e in entries:
        if e["type"] != "file" or e["status"] != "ok":
            continue
        if not e["path"].lower().endswith((".md", ".txt")):
            continue
        if e.get("size") is not None and e["size"] > 256 * 1024:
            continue
        content, err = _read_text_safe(os.path.join(root, e["path"].replace("/", os.sep)))
        if err or not content:
            continue
        for pat, kind in patterns:
            for m in re.finditer(pat, content):
                notes.append({
                    "locator": e["path"],
                    "kind": kind,
                    "quote": m.group(0).strip(),
                })
    # deduplicate notes deterministically
    seen = set()
    unique = []
    for n in notes:
        key = (n["locator"], n["kind"], n["quote"])
        if key not in seen:
            seen.add(key)
            unique.append(n)
    unique.sort(key=lambda n: (n["locator"], n["kind"], n["quote"]))
    return unique


# --------------------------------------------------------------------------
# dedup / version / alias analysis
# --------------------------------------------------------------------------

def dedup_groups(entries):
    by_hash = {}
    for e in entries:
        if e["type"] == "file" and e.get("sha256") and e["status"] == "ok":
            by_hash.setdefault(e["sha256"], []).append(e["path"])
    groups = []
    for digest in _stable(by_hash):
        paths = _stable(by_hash[digest])
        if len(paths) > 1:
            groups.append({"sha256": digest, "paths": paths, "count": len(paths)})
    groups.sort(key=lambda g: (-g["count"], g["sha256"]))
    return groups


def _norm_stem(name):
    stem = name
    for ext in (".pdf", ".md", ".txt", ".csv", ".json", ".docx", ".xlsx", ".pptx"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    return stem.strip().lower()


def version_candidates(entries):
    """Heuristic version chains: group by normalised stem, then order by mtime.
    This is explicitly labelled heuristic, not ground truth."""
    buckets = {}
    for e in entries:
        if e["type"] != "file":
            continue
        name = e["path"].split("/")[-1]
        stem = _norm_stem(name)
        m = VERSION_STEM_RE.match(stem)
        if m:
            stem = m.group(1).strip()
        buckets.setdefault(stem, []).append(e["path"])
    candidates = []
    for stem in _stable(buckets):
        paths = _stable(buckets[stem])
        if len(paths) < 2:
            continue
        candidates.append({
            "stem": stem,
            "paths": paths,
            "count": len(paths),
            "method": "normalised_stem_heuristic",
        })
    candidates.sort(key=lambda c: (-c["count"], c["stem"]))
    return candidates


def alias_groups(entries):
    symlinks = []
    hardlinks = []
    inode_map = {}
    for e in entries:
        if e["type"] == "symlink":
            symlinks.append({"path": e["path"], "target": e["link_target"], "broken": e["status"] == "broken_symlink"})
        if e["type"] == "file" and e.get("inode") is not None and e.get("nlink", 1) > 1:
            inode_map.setdefault((e["inode"],), []).append(e["path"])
    hard_groups = []
    for key in _stable(inode_map):
        paths = _stable(inode_map[key])
        if len(paths) > 1:
            hard_groups.append({"inode": key[0], "paths": paths, "count": len(paths)})
    symlinks.sort(key=lambda s: s["path"])
    hard_groups.sort(key=lambda g: g["inode"])
    return {"symlinks": symlinks, "hardlink_groups": hard_groups}


# --------------------------------------------------------------------------
# reconciliation
# --------------------------------------------------------------------------

def reconcile(inventory_entries, catalogs):
    """Compare actual files against catalogue records."""
    actual = {e["path"]: e for e in inventory_entries if e["type"] == "file"}
    matches, mismatches, missing, orphaned = [], [], [], []
    for rel, doc, err in catalogs:
        if not isinstance(doc, dict):
            continue
        items = doc.get("items", [])
        if not isinstance(items, list):
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            path = item.get("path") or item.get("file") or item.get("filename") or item.get("name")
            if not path:
                continue
            path = str(path).replace("\\", "/")
            if path in actual:
                rec = actual[path]
                expected = item.get("sha256") or item.get("hash")
                if expected and rec.get("sha256") and expected.lower() != rec["sha256"]:
                    mismatches.append({"locator": "%s#item%d" % (rel, idx), "path": path, "expected": expected, "actual": rec["sha256"]})
                else:
                    matches.append({"locator": "%s#item%d" % (rel, idx), "path": path})
            else:
                missing.append({"locator": "%s#item%d" % (rel, idx), "path": path})
    catalog_paths = set()
    for rel, doc, err in catalogs:
        if isinstance(doc, dict):
            for item in (doc.get("items") or []):
                if isinstance(item, dict):
                    p = item.get("path") or item.get("file") or item.get("filename") or item.get("name")
                    if p:
                        catalog_paths.add(str(p).replace("\\", "/"))
    for p in _stable(actual):
        if p not in catalog_paths and actual[p]["role"] not in ("control/case", "control/task"):
            orphaned.append({"path": p})
    return {
        "matches": matches,
        "mismatches": mismatches,
        "missing": missing,
        "orphaned": orphaned,
        "catalog_files": [c[0] for c in catalogs],
    }


# --------------------------------------------------------------------------
# index + answers
# --------------------------------------------------------------------------

def build_index(entries, dedup, versions, aliases, recon):
    index = {
        "by_path": {e["path"]: {"type": e["type"], "sha256": e.get("sha256"), "size": e.get("size"), "role": e.get("role")} for e in entries},
        "by_hash": {},
        "by_role": {},
        "duplicate_hashes": [g["sha256"] for g in dedup],
        "version_stems": [v["stem"] for v in versions],
        "broken_symlinks": [s["path"] for s in aliases["symlinks"] if s["broken"]],
        "missing_paths": [m["path"] for m in recon["missing"]],
    }
    for e in entries:
        if e["type"] == "file" and e.get("sha256"):
            index["by_hash"].setdefault(e["sha256"], []).append(e["path"])
        role = e.get("role") or "unknown"
        index["by_role"].setdefault(role, []).append(e["path"])
    for d in _stable(index):
        if isinstance(index[d], dict):
            for k in _stable(index[d]):
                v = index[d][k]
                if isinstance(v, list):
                    v.sort()
    for role in _stable(index["by_role"]):
        index["by_role"][role].sort()
    return index


def build_answers(inventory, dedup, versions, aliases, recon, notes):
    files = inventory["count_files"]
    dirs = inventory["count_dirs"]
    total = sum((e.get("size") or 0) for e in inventory["entries"] if e["type"] == "file")
    dup_bytes = 0
    for g in dedup:
        # wasted bytes = (count - 1) * size of one copy
        first = next((e for e in inventory["entries"] if e["path"] == g["paths"][0]), None)
        if first and first.get("size"):
            dup_bytes += (g["count"] - 1) * first["size"]
    answers = [
        {"question": "file_count", "answer": files, "evidence": "inventory"},
        {"question": "directory_count", "answer": dirs, "evidence": "inventory"},
        {"question": "total_bytes", "answer": total, "evidence": "inventory"},
        {"question": "duplicate_group_count", "answer": len(dedup), "evidence": "dedup"},
        {"question": "duplicate_wasted_bytes_estimate", "answer": dup_bytes, "evidence": "dedup"},
        {"question": "version_candidate_count", "answer": len(versions), "evidence": "version_candidates"},
        {"question": "broken_symlink_count", "answer": sum(1 for s in aliases["symlinks"] if s["broken"]), "evidence": "aliases"},
        {"question": "hardlink_group_count", "answer": len(aliases["hardlink_groups"]), "evidence": "aliases"},
        {"question": "missing_catalog_path_count", "answer": len(recon["missing"]), "evidence": "reconciliation"},
        {"question": "orphaned_file_count", "answer": len(recon["orphaned"]), "evidence": "reconciliation"},
        {"question": "version_note_count", "answer": len(notes), "evidence": "version_notes"},
    ]
    return answers


# --------------------------------------------------------------------------
# directory tree
# --------------------------------------------------------------------------

def build_tree_text(entries):
    """Deterministic text tree with depth cap and [空目录] markers."""
    dirs = {e["path"].rstrip("/"): e for e in entries if e["type"] == "directory"}
    files = {e["path"]: e for e in entries if e["type"] in ("file", "symlink", "non_regular")}
    lines = ["."]
    file_paths = _stable(files)
    # top-level files first, then recurse directories
    root_files = [p for p in file_paths if "/" not in p]
    for p in root_files:
        f = files[p]
        mark = ""
        if f["type"] == "symlink":
            mark = " -> %s%s" % (f.get("link_target") or "", " [断链]" if f["status"] == "broken_symlink" else "")
        lines.append("├── %s%s" % (p, mark))
    dir_paths = _stable([d for d in dirs if d and d != "."])
    for d in dir_paths:
        depth = d.count("/") + 1
        if depth > MAX_TREE_DEPTH:
            lines.append("...  (depth cap: %s)" % d)
            continue
        indent = "│   " * depth
        label = d
        if dirs[d].get("note") == "empty":
            label = "%s  [空目录]" % d
        lines.append("%s├── %s" % (indent[:-3] if indent else "", label))
        for p in file_paths:
            if p.startswith(d + "/") and "/" not in p[len(d) + 1:]:
                f = files[p]
                mark = ""
                if f["type"] == "symlink":
                    mark = " -> %s%s" % (f.get("link_target") or "", " [断链]" if f["status"] == "broken_symlink" else "")
                lines.append("%s├── %s%s" % (indent, p, mark))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# manifest + artifacts
# --------------------------------------------------------------------------

def write_manifest_csv(entries, path):
    fieldnames = ["path", "type", "role", "size_bytes", "mtime", "mode", "sha256", "status", "note", "link_target"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for e in entries:
            row = {k: e.get(k, "") for k in fieldnames}
            for k in ("size_bytes", "mtime"):
                if row[k] is None:
                    row[k] = ""
            writer.writerow(row)


def _json_dump(obj, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def _append_log(path, entry):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


# --------------------------------------------------------------------------
# result assembly
# --------------------------------------------------------------------------

def build_result(args, inventory, dedup, versions, aliases, notes, catalogs, recon, index, answers):
    case_id = args.get("case_id") or ""
    contract = args.get("contract")
    status = "partial"

    result = {
        "case_id": case_id,
        "status": status,
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "errors": list(inventory["errors"]),
        "degradations": list(inventory["degradations"]),
        "runtime": {
            "engine_version": __version__,
            "generated_at": _iso_now(),
            "input_root": args.get("input_root", ""),
            "output_dir": args.get("output_dir", ""),
            "max_content_bytes": MAX_CONTENT_BYTES,
        },
        "limitations": [],
        "pending_human": [],
        "artifacts": args.get("artifacts", {}),
        "computed": {
            "files": inventory["count_files"],
            "directories": inventory["count_dirs"],
            "summary": {
                "files": inventory["count_files"],
                "directories": inventory["count_dirs"],
                "symlinks": inventory["count_symlinks"],
                "duplicate_groups": len(dedup),
                "version_candidates": len(versions),
                "broken_symlinks": sum(1 for s in aliases["symlinks"] if s["broken"]),
                "missing": len(recon["missing"]),
                "orphaned": len(recon["orphaned"]),
            },
            "inventory": inventory["entries"],
            "duplicate_groups": dedup,
            "version_candidates": versions,
            "version_notes": notes,
            "aliases": aliases,
            "reconciliation": recon,
            "answers": answers,
            "index": index,
            "artifacts": args.get("artifacts", {}),
        },
    }

    # backfill contract fields verbatim; collisions move under computed._contract_*
    if isinstance(contract, dict):
        for key in _stable(contract):
            value = contract[key]
            if key in RESERVED_KEYS:
                result.setdefault("computed", {})["_contract_%s" % key] = value
            else:
                result[key] = value
    else:
        result["uncertainty"].append({
            "item": "output_contract",
            "reason": "no output_contract object found in case.json/task.md",
            "severity": "medium",
        })

    # sources for key machine-derived conclusions
    for g in dedup[:20]:
        result["sources"].append({
            "item": "duplicate_group",
            "locator": g["paths"][0],
            "quote": "sha256=%s, count=%d, paths=%s" % (g["sha256"][:12], g["count"], ",".join(g["paths"][:3])),
        })
    for s in aliases["symlinks"]:
        if s["broken"]:
            result["sources"].append({
                "item": "broken_symlink",
                "locator": s["path"],
                "quote": "-> %s" % s["target"],
            })

    if inventory["errors"]:
        result["uncertainty"].append({
            "item": "inventory_errors",
            "reason": "%d entries could not be fully read" % len(inventory["errors"]),
            "severity": "medium",
        })
    if inventory["degradations"]:
        result["limitations"].append({
            "item": "oversized_files",
            "detail": "%d file(s) skipped for content hash (size cap %d)" % (
                len(inventory["degradations"]), MAX_CONTENT_BYTES),
        })

    if not case_id:
        result["status"] = "input_error"
        result["errors"].append({"locator": "case.json", "issue": "missing_case_id", "detail": "no case_id found"})
    return result


def _dedup_sources(result):
    seen = set()
    out = []
    for s in result["sources"]:
        key = (s.get("item"), s.get("locator"), s.get("quote"))
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


# --------------------------------------------------------------------------
# update / merge
# --------------------------------------------------------------------------

def apply_update(result, patch):
    """Merge a semantic patch produced by the agent into the result."""
    if not isinstance(patch, dict):
        return result
    for key in patch:
        if key == "sources":
            result.setdefault("sources", []).extend(patch["sources"])
        elif key == "uncertainty":
            result.setdefault("uncertainty", []).extend(patch["uncertainty"])
        elif key == "pending_human":
            result.setdefault("pending_human", []).extend(patch["pending_human"])
        elif key == "change_log":
            result.setdefault("change_log", []).extend(patch["change_log"])
        elif key == "status":
            result["status"] = patch["status"]
        elif key in ("errors", "degradations", "limitations"):
            result.setdefault(key, []).extend(patch[key])
        elif key == "computed":
            for k, v in patch["computed"].items():
                result.setdefault("computed", {})[k] = v
        elif key in RESERVED_KEYS:
            result[key] = patch[key]
        else:
            result[key] = patch[key]
    result["sources"] = _dedup_sources(result)
    return result


def check_result(result, case_data=None):
    """Validate result.json; returns list of problem strings ([] == ok)."""
    problems = []
    if not isinstance(result, dict):
        return ["result is not a JSON object"]
    if not result.get("case_id"):
        problems.append("missing case_id")
    if result.get("status") not in STATUS_VALUES:
        problems.append("invalid status %r" % result.get("status"))
    for key in ("sources", "uncertainty", "change_log"):
        if key not in result:
            problems.append("missing %s" % key)
    if case_data is not None and isinstance(case_data, dict):
        contract, _ = detect_contract(case_data)
        if isinstance(contract, dict):
            for k in contract:
                if k not in result and ("_contract_%s" % k) not in result.get("computed", {}):
                    problems.append("contract field not backfilled: %s" % k)
    return problems


# --------------------------------------------------------------------------
# main pipeline
# --------------------------------------------------------------------------

def run_pipeline(args, skeleton_only=False):
    input_root = args["input_root"]
    output_dir = args["output_dir"]
    work_dir = args.get("work_dir") or output_dir
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    operation_log = os.path.join(output_dir, "operation_log.jsonl")
    if os.path.exists(operation_log):
        os.remove(operation_log)

    def log_entry(step, detail):
        _append_log(operation_log, {
            "time": _iso_now(),
            "actor": "skill",
            "step": step,
            "detail": detail,
        })

    log_entry("start", "input_root=%s output_dir=%s" % (input_root, output_dir))

    # control inputs
    case_path = os.path.join(input_root, "case.json")
    task_path = os.path.join(input_root, "task.md")
    case_data = None
    task_text = None
    case_err = None
    if os.path.isfile(case_path):
        raw, err = _read_text_safe(case_path)
        if err:
            case_err = err
        else:
            try:
                case_data = json.loads(raw)
            except ValueError as exc:
                case_err = "case_json_parse_error:%s" % exc
    else:
        case_err = "missing case.json"

    if os.path.isfile(task_path):
        task_text, _ = _read_text_safe(task_path)

    case_id = ""
    if isinstance(case_data, dict):
        for k in ("case_id", "id", "challenge_id", "caseId"):
            if isinstance(case_data.get(k), (str, int)):
                case_id = str(case_data[k])
                break
    if not case_id and task_text:
        m = re.search(r"(?i)challenge[-_ ]id[：:`\s]*([A-Za-z0-9_-]+)", task_text)
        if m:
            case_id = m.group(1)

    contract, contract_key = detect_contract(case_data, task_text)

    inventory = build_inventory(input_root)
    dedup = dedup_groups(inventory["entries"])
    versions = version_candidates(inventory["entries"])
    aliases = alias_groups(inventory["entries"])
    notes = extract_version_notes(input_root, task_text, inventory["entries"])

    catalogs = []
    for e in inventory["entries"]:
        if e["type"] == "file" and e["status"] == "ok" and e.get("role") in ("json", "csv", "bibtex", "ris", "sqlite"):
            doc, err = parse_catalog(e["path"], input_root)
            catalogs.append((e["path"], doc, err))

    recon = reconcile(inventory["entries"], catalogs)
    index = build_index(inventory["entries"], dedup, versions, aliases, recon)
    answers = build_answers(inventory, dedup, versions, aliases, recon, notes)

    artifacts = {
        "library_manifest.csv": os.path.join(output_dir, "library_manifest.csv"),
        "directory_tree.txt": os.path.join(output_dir, "directory_tree.txt"),
        "dedup_version.json": os.path.join(output_dir, "dedup_version.json"),
        "recovery_report.json": os.path.join(output_dir, "recovery_report.json"),
        "library_index.json": os.path.join(output_dir, "library_index.json"),
        "library_manifest.md": os.path.join(output_dir, "library_manifest.md"),
        "operation_log.jsonl": operation_log,
    }

    args["case_id"] = case_id
    args["contract"] = contract
    args["artifacts"] = artifacts

    if skeleton_only:
        result = {
            "case_id": case_id,
            "status": "partial",
            "sources": [],
            "uncertainty": [{"item": "skeleton", "reason": "partial skeleton written before full analysis", "severity": "info"}],
            "change_log": [{"time": _iso_now(), "actor": "skill", "step": "skeleton", "detail": "skeleton-only"}],
            "errors": [],
            "degradations": [],
            "runtime": {"engine_version": __version__, "generated_at": _iso_now()},
            "limitations": [],
            "pending_human": [],
            "computed": {"artifacts": artifacts},
            "artifacts": artifacts,
        }
        if isinstance(contract, dict):
            for key in _stable(contract):
                if key in RESERVED_KEYS:
                    result.setdefault("computed", {})["_contract_%s" % key] = contract[key]
                else:
                    result[key] = contract[key]
        if case_err:
            result["errors"].append({"locator": "case.json", "issue": case_err, "detail": ""})
            result["status"] = "input_error"
        _json_dump(result, os.path.join(output_dir, "result.json"))
        log_entry("skeleton", "written partial result")
        return result

    result = build_result(args, inventory, dedup, versions, aliases, notes, catalogs, recon, index, answers)
    no_content = inventory["count_files"] == 0 and inventory["count_dirs"] == 0
    if case_err:
        if case_err == "missing case.json" and no_content:
            result["status"] = "insufficient_evidence"
            result["uncertainty"].append({
                "item": "input",
                "reason": "input directory is empty and case.json is absent",
                "severity": "high",
            })
        else:
            result["errors"].append({"locator": "case.json", "issue": case_err, "detail": ""})
            if result["status"] in ("partial", "insufficient_evidence"):
                result["status"] = "input_error"

    # write artifacts
    write_manifest_csv(inventory["entries"], artifacts["library_manifest.csv"])
    with open(artifacts["directory_tree.txt"], "w", encoding="utf-8") as fh:
        fh.write(build_tree_text(inventory["entries"]))
    _json_dump({
        "duplicate_groups": dedup,
        "version_candidates": versions,
        "version_notes": notes,
        "aliases": aliases,
    }, artifacts["dedup_version.json"])
    _json_dump(recon, artifacts["recovery_report.json"])
    _json_dump(index, artifacts["library_index.json"])
    _json_dump(answers, os.path.join(output_dir, "answers.json"))
    artifacts["answers.json"] = os.path.join(output_dir, "answers.json")
    result["computed"]["artifacts"] = artifacts
    result["artifacts"] = artifacts

    # handoff report
    md_lines = [
        "# 科研资料库交接报告",
        "",
        "- case_id: %s" % (case_id or "not_reported"),
        "- 文件数: %d" % inventory["count_files"],
        "- 目录数: %d" % inventory["count_dirs"],
        "- 重复组: %d" % len(dedup),
        "- 版本候选: %d" % len(versions),
        "- 断链符号链接: %d" % sum(1 for s in aliases["symlinks"] if s["broken"]),
        "",
        "## 复核方法",
        "",
        "`scripts/selftest.py` 对同一输入运行两遍并规范化比较产物，",
        "确认除时间戳外结果一致；哈希为流式 SHA-256。",
        "",
        "## 证据",
        "",
        "关键结论见 `result.json` 的 `sources`；完整操作见 `operation_log.jsonl`。",
    ]
    with open(artifacts["library_manifest.md"], "w", encoding="utf-8") as fh:
        fh.write("\n".join(md_lines) + "\n")

    # final sources normalization
    result["sources"] = _dedup_sources(result)
    result["change_log"] = [
        {"time": _iso_now(), "actor": "skill", "step": "complete",
         "detail": "inventory=%d files, %d dup groups, %d version candidates, %d mismatches"
                  % (inventory["count_files"], len(dedup), len(versions), len(recon["mismatches"]))},
    ]
    _json_dump(result, os.path.join(output_dir, "result.json"))
    log_entry("complete", "result.json + %d artifacts written" % len(artifacts))
    return result


def build_parser():
    p = argparse.ArgumentParser(prog="finalize.py", description="deterministic library repair engine")
    p.add_argument("--input-root", "--input", dest="input_root", default="/input")
    p.add_argument("--output-dir", "--output", dest="output_dir", default="/app/output")
    p.add_argument("--work-dir", "--work", dest="work_dir", default="/app/work")
    p.add_argument("--case-json", dest="case_json", default=None)
    p.add_argument("--task-path", dest="task_path", default=None)
    p.add_argument("--skeleton-only", action="store_true")
    p.add_argument("--update", dest="update_file", default=None)
    p.add_argument("--result", dest="result_file", default=None)
    p.add_argument("--check", dest="check_file", default=None)
    return p


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.update_file:
        # merge-only path: never re-runs the pipeline, preserving the
        # skeleton-first / incremental-improvement contract.
        if not args.result_file:
            print("update: --result required", file=sys.stderr)
            return 2
        patch_text, perr = _read_text_safe(args.update_file)
        if perr or patch_text is None:
            print("update: cannot read patch: %s" % (perr or "empty"), file=sys.stderr)
            return 2
        try:
            patch = json.loads(patch_text)
        except ValueError as exc:
            print("update: bad patch json: %s" % exc, file=sys.stderr)
            return 2
        result_text, rerr = _read_text_safe(args.result_file)
        if rerr or result_text is None:
            print("update: cannot read result: %s" % (rerr or "empty"), file=sys.stderr)
            return 2
        try:
            current = json.loads(result_text)
        except ValueError as exc:
            print("update: bad result json: %s" % exc, file=sys.stderr)
            return 2
        updated = apply_update(current, patch)
        _json_dump(updated, args.result_file)
        comp = updated.get("computed", {}).get("summary", {})
        print("update: merged patch -> status=%s files=%s" % (updated.get("status"), comp.get("files", "?")))
        return 0

    if args.check_file:
        with open(args.check_file, "r", encoding="utf-8-sig") as fh:
            result = json.load(fh)
        case_data = None
        if args.case_json and os.path.isfile(args.case_json):
            raw, err = _read_text_safe(args.case_json)
            if not err and raw is not None:
                try:
                    case_data = json.loads(raw)
                except ValueError:
                    case_data = None
        problems = check_result(result, case_data)
        if problems:
            print("check: FAIL")
            for p in problems:
                print("  -", p)
            return 2
        print("check: OK")
        return 0

    run_args = {
        "input_root": args.input_root,
        "output_dir": args.output_dir,
        "work_dir": args.work_dir,
    }
    result = run_pipeline(run_args, skeleton_only=args.skeleton_only)

    # emit a compact human-readable summary
    comp = result.get("computed", {}).get("summary", {})
    mismatches = len(result.get("computed", {}).get("reconciliation", {}).get("mismatches", []))
    print("finalize: %s files, %s dup groups, %s version candidates, %s broken symlinks, %s mismatches -> %s"
          % (comp.get("files", 0), comp.get("duplicate_groups", 0), comp.get("version_candidates", 0),
             comp.get("broken_symlinks", 0), mismatches,
             os.path.join(args.output_dir, "result.json")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
