#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py - deterministic library-repair pipeline.

Design (intentional divergence from a single monolithic finalize script)
=========================================================================
The pipeline is stage-based and centered on one canonical *intermediate
representation* (IR). Every stage is a pure function over the IR and writes
the IR back atomically. Semantic conclusions from the agent are kept out of
the code path: they arrive as a `decisions.json` overlay and are merged at
emission time, so the mechanical layers stay reproducible and the human/AI
judgements stay auditable.

    discovery -> normalization -> IR assembly -> mechanical analysis stages
    -> decision overlay -> emission -> validation

Validation order is contract-first: a skeleton result with all contract keys
is written *before* the heavy scan, and `--check` re-validates the finished
result against the same contract before exit.

Only the Python standard library is used. Iteration order is always sorted;
no randomness is used; re-running on the same input reproduces the same
canonical content (timestamps in the change log are the only allowed
variance).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import platform
import re
import sqlite3
import stat as stat_mod
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants and small utilities
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.0.0"
MAX_TEXT_SCAN_BYTES = 2 * 1024 * 1024      # skip content hashing beyond this
MAX_PREVIEW_CHARS = 240                    # preview kept in the IR/artifacts
RESERVED_KEYS = frozenset({
    "case_id", "status", "sources", "uncertainty", "change_log", "errors",
    "degradations", "runtime", "limitations", "pending_human", "computed",
    "artifacts", "decisions",
})
CONTRACT_CANDIDATE_KEYS = (
    "output_contract", "output", "result_schema", "contract",
    "outputContract", "resultSchema", "output_schema",
)
TEXT_EXTENSIONS = {
    ".md", ".txt", ".py", ".yaml", ".yml", ".json", ".jsonl", ".csv", ".tsv",
    ".xml", ".html", ".htm", ".log", ".bib", ".ris", ".tex", ".r", ".m",
    ".ini", ".cfg", ".toml", ".sh", ".c", ".h", ".cpp", ".hpp", ".java",
    ".go", ".rs", ".sql", ".tsv", ".rst", ".adoc",
}
CATALOG_EXTENSIONS = {
    ".json": "json", ".csv": "csv", ".tsv": "csv", ".bib": "bibtex",
    ".ris": "ris", ".db": "sqlite", ".sqlite": "sqlite", ".sqlite3": "sqlite",
}
VERSION_TOKENS = re.compile(
    r"(\bv?\d+(?:[._-]\d+)+(?:[-_a-zA-Z0-9]*)\b|"
    r"\b(?:draft|final|rev(?:ision)?\s?\d*|copy|backup|old|new|latest|"
    r"ver(?:sion)?\s?\d*)\b|"
    r"\b\d{4}[-_]\d{1,2}[-_]\d{1,2}\b|"
    r"\((?:\d+|[a-z])\)|"
    r"[._-](?:old|new|bak|final|draft|copy\d*)\b)",
    re.IGNORECASE,
)
NON_ALNUM = re.compile(r"[^0-9a-z]+")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _atomic_write_text(path: str, text: str) -> None:
    """Crash-safe write: write a sibling temp file then os.replace()."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".tmp." + os.path.basename(path) + "." +
                       str(os.getpid()))
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _atomic_write_json(path: str, obj: Any) -> None:
    _atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2,
                                        sort_keys=True) + "\n")


def _norm_rel(path: str) -> str:
    """Normalize a path to a forward-slash relative form for sorting/keys."""
    return path.replace(os.sep, "/").lstrip("./")


def _sha256_file(path: str, size: int) -> Optional[str]:
    if size > MAX_TEXT_SCAN_BYTES:
        return None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 256), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _read_text_robust(path: str, size: int) -> Tuple[Optional[str], str, Optional[str]]:
    """Return (text, encoding, degradation). Tries utf-8-sig then latin-1."""
    if size > MAX_TEXT_SCAN_BYTES:
        return None, "skipped:too_large", "too_large"
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "unreadable", "unreadable:%s" % exc
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), enc, None
        except UnicodeDecodeError:
            continue
    # latin-1 never fails; it is the honest "bytes preserved" fallback.
    return raw.decode("latin-1"), "latin-1", "bad_encoding:used_latin-1"


def _mode_string(mode: int) -> str:
    perms = "".join(
        ("r" if mode & bit else "-") for bit in
        (stat_mod.S_IRUSR, stat_mod.S_IWUSR, stat_mod.S_IXUSR,
         stat_mod.S_IRGRP, stat_mod.S_IWGRP, stat_mod.S_IXGRP,
         stat_mod.S_IROTH, stat_mod.S_IWOTH, stat_mod.S_IXOTH)
    )
    return perms


def _rwx(mode: int) -> str:
    return _mode_string(mode)


def _inode_key(st) -> Optional[Tuple[int, int]]:
    try:
        ino = getattr(st, "st_ino", 0)
        dev = getattr(st, "st_dev", 0)
        if ino and st.st_nlink > 1:
            return (dev, ino)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Contract discovery and backfill
# ---------------------------------------------------------------------------

def _find_contract(case: Any, task_text: str) -> Tuple[Optional[Any], List[str]]:
    """Return (contract_value, contract_keys)."""
    contract = None
    keys: List[str] = []
    if isinstance(case, dict):
        for k in CONTRACT_CANDIDATE_KEYS:
            if k in case and case[k] is not None:
                contract = case[k]
                break
        # A flat dict of expected result fields is also a plausible contract.
        if contract is None and case.get("output_contract") is None:
            # Do not guess from arbitrary keys; only explicit contract keys.
            pass
    # A fenced JSON block in task.md headed by a contract keyword.
    if contract is None and task_text:
        m = re.search(
            r"```(?:json)?\s*\n?\s*(?:output_contract|contract|result_schema)"
            r"\s*[:=]\s*\n?(.*?)```",
            task_text, re.IGNORECASE | re.DOTALL)
        if m:
            try:
                contract = json.loads(m.group(1))
            except json.JSONDecodeError:
                contract = None

    if isinstance(contract, dict):
        keys = [str(k) for k in contract.keys()]
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                keys.extend(str(k) for k in item.keys())
    # De-duplicate, preserve order.
    seen = set()
    keys = [k for k in keys if not (k in seen or seen.add(k))]
    return contract, keys


def _contract_placeholder(contract: Any, key: str) -> Any:
    """Return the honest placeholder for one contract key (null or [])."""
    if isinstance(contract, dict):
        spec = contract.get(key)
        if isinstance(spec, dict):
            return {str(k): _contract_placeholder(spec, str(k)) for k in spec}
        if isinstance(spec, list):
            return []
        if isinstance(spec, str) and "list" in spec.lower():
            return []
    return None


# ---------------------------------------------------------------------------
# Discovery and normalization
# ---------------------------------------------------------------------------

def _classify(rel: str, size: int, text: Optional[str], ext: str) -> str:
    if ext in CATALOG_EXTENSIONS:
        return CATALOG_EXTENSIONS[ext]
    if ext in (".pdf", ".PDF"):
        return "pdf"
    if ext in TEXT_EXTENSIONS or ext in (".pdf",):
        return "text" if ext in TEXT_EXTENSIONS else "pdf"
    if text is not None:
        # Sniff: if the first bytes decoded cleanly and look textual, say text.
        sample = text[:512]
        printable = sum(1 for ch in sample if ch in "\t\n\r" or 32 <= ord(ch) < 127 or ord(ch) > 127)
        if sample and printable / max(len(sample), 1) > 0.90:
            return "text"
    return "binary"


def _scan(root: str) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    entries: List[Dict[str, Any]] = []
    errors: List[str] = []
    degradations: List[str] = []
    if not os.path.isdir(root):
        errors.append("input_root_missing:%s" % root)
        return entries, errors, degradations

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        for name in dirnames:
            full = os.path.join(dirpath, name)
            rel = _norm_rel(os.path.relpath(full, root))
            try:
                st = os.lstat(full)
            except OSError as exc:
                errors.append("lstat_failed:%s:%s" % (rel, exc))
                continue
            kind = "dir"
            if os.path.islink(full):
                kind = "symlink"
                try:
                    target = os.readlink(full)
                except OSError as exc:
                    target = None
                    errors.append("readlink_failed:%s:%s" % (rel, exc))
                broken = not os.path.exists(full)
                entries.append({
                    "path": rel, "kind": "symlink" if not broken else "broken_link",
                    "link_target": target,
                    "size": getattr(st, "st_size", 0),
                    "mtime": getattr(st, "st_mtime", None),
                    "mode": stat_mod.S_IMODE(getattr(st, "st_mode", 0)),
                    "broken": broken,
                    "sha256": None, "content_kind": None,
                    "encoding": None, "extension": os.path.splitext(name)[1],
                    "basename": name, "parent": _norm_rel(dirpath) or ".",
                    "depth": rel.count("/") + 1, "flags": [],
                })
                continue
            entries.append({
                "path": rel, "kind": "dir", "link_target": None,
                "size": 0, "mtime": getattr(st, "st_mtime", None),
                "mode": stat_mod.S_IMODE(getattr(st, "st_mode", 0)),
                "broken": False, "sha256": None, "content_kind": None,
                "encoding": None, "extension": "",
                "basename": name, "parent": _norm_rel(dirpath) or ".",
                "depth": rel.count("/") + 1, "flags": [],
            })
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = _norm_rel(os.path.relpath(full, root))
            entry: Dict[str, Any] = {
                "path": rel, "kind": "file", "link_target": None,
                "size": 0, "mtime": None, "mode": 0, "broken": False,
                "sha256": None, "content_kind": None, "encoding": None,
                "extension": os.path.splitext(name)[1], "basename": name,
                "parent": _norm_rel(dirpath) or ".", "depth": rel.count("/") + 1,
                "flags": [],
            }
            try:
                st = os.lstat(full)
            except OSError as exc:
                entry["flags"].append("unreadable")
                errors.append("lstat_failed:%s:%s" % (rel, exc))
                entries.append(entry)
                continue
            entry["size"] = getattr(st, "st_size", 0)
            entry["mtime"] = getattr(st, "st_mtime", None)
            entry["mode"] = stat_mod.S_IMODE(getattr(st, "st_mode", 0))
            if os.path.islink(full):
                # A directory entry classified as a file but actually a symlink.
                entry["kind"] = "symlink"
                try:
                    entry["link_target"] = os.readlink(full)
                except OSError as exc:
                    errors.append("readlink_failed:%s:%s" % (rel, exc))
                if not os.path.exists(full):
                    entry["kind"] = "broken_link"
                    entry["broken"] = True
                entries.append(entry)
                continue
            if not stat_mod.S_ISREG(getattr(st, "st_mode", 0)):
                entry["kind"] = "special"
                entry["flags"].append("non_regular")
                entries.append(entry)
                continue
            # Content: hash + optional text decode.
            entry["sha256"] = _sha256_file(full, entry["size"])
            text, enc, degrade = _read_text_robust(full, entry["size"])
            if degrade == "too_large":
                entry["flags"].append("too_large")
                degradations.append("too_large:skipped_content:%s" % rel)
            elif degrade and degrade.startswith("unreadable"):
                entry["flags"].append("unreadable")
                errors.append("unreadable:%s" % rel)
            elif degrade == "bad_encoding:used_latin-1":
                entry["flags"].append("bad_encoding")
                degradations.append("bad_encoding:latin-1_fallback:%s" % rel)
            entry["encoding"] = enc
            if text is not None:
                entry["text_preview"] = text[:MAX_PREVIEW_CHARS]
                entry["lines"] = text.count("\n") + (0 if text.endswith("\n") else 1)
            entry["content_kind"] = _classify(rel, entry["size"], text, entry["extension"])
            # Inode key for hardlink grouping.
            inok = _inode_key(st)
            if inok is not None:
                entry["inode"] = [inok[0], inok[1]]
            entries.append(entry)

    entries.sort(key=lambda e: (e["path"], e["kind"]))
    return entries, errors, degradations


# ---------------------------------------------------------------------------
# Catalog parsers (JSON / CSV / BibTeX / RIS / SQLite)
# ---------------------------------------------------------------------------

def _parse_catalog(entry: Dict[str, Any], abs_path: str, root: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Return (records, error). Records carry declared identifiers + fields."""
    kind = entry.get("content_kind")
    size = entry.get("size", 0)
    if size > MAX_TEXT_SCAN_BYTES:
        return [], "too_large"
    text, _, _ = _read_text_robust(abs_path, size)
    if text is None:
        return [], "unreadable"

    def rec(**fields: Any) -> Dict[str, Any]:
        rec0 = {"_catalog": entry["path"]}
        rec0.update(fields)
        return rec0

    try:
        if kind == "json":
            data = json.loads(text)
            if isinstance(data, list):
                out = []
                for i, item in enumerate(data):
                    if isinstance(item, dict):
                        out.append(rec(_index=i, **item))
                    else:
                        out.append(rec(_index=i, _value=item))
                return out, None
            if isinstance(data, dict):
                # A map keyed by path is a common manifest shape.
                for list_key in ("files", "entries", "items", "records", "manifest"):
                    if isinstance(data.get(list_key), list):
                        return [rec(_index=i, **it) if isinstance(it, dict)
                                else rec(_index=i, _value=it)
                                for i, it in enumerate(data[list_key])], None
                return [rec(**data)], None
            return [], None
        if kind == "csv":
            sample = text[:4096]
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
            return [rec(_index=i, **r) for i, r in enumerate(rows)], None
        if kind == "bibtex":
            return _parse_bibtex(text, entry["path"]), None
        if kind == "ris":
            return _parse_ris(text, entry["path"]), None
        if kind == "sqlite":
            return _parse_sqlite(abs_path), None
    except Exception as exc:  # noqa: BLE001 - degrade, never crash
        return [], "%s_parse_error:%s" % (kind, exc)
    return [], None


def _parse_bibtex(text: str, path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    # Lightweight BibTeX: one @type{key, fields} block at a time.
    pat = re.compile(r"@(\w+)\s*\{\s*([^,{]+),", re.IGNORECASE)
    for m in pat.finditer(text):
        fields: Dict[str, Any] = {"_catalog": path, "_type": m.group(1),
                                  "_key": m.group(2).strip()}
        # Slurp the balanced brace block.
        depth = 0
        start = m.end() - 1
        body = []
        for ch in text[start:]:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            body.append(ch)
        body_str = "".join(body)
        for fm in re.finditer(r"(\w+)\s*=\s*[{\"]([^{}\"]*)[}\"]", body_str):
            fields[fm.group(1).lower()] = fm.group(2)
        out.append(fields)
    return out


def _parse_ris(text: str, path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    cur: Dict[str, Any] = {}
    for line in text.splitlines():
        if len(line) < 6 or line[:2] != "  ":
            continue
        tag, val = line[:2].strip(), line[6:].strip()
        if tag == "ER" and cur:
            cur["_catalog"] = path
            out.append(cur)
            cur = {}
        elif val:
            key = tag.lower()
            if key in cur:
                cur[key] = cur[key] + "; " + val
            else:
                cur[key] = val
    if cur:
        cur["_catalog"] = path
        out.append(cur)
    return out


def _parse_sqlite(abs_path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % abs_path, uri=True)
    except sqlite3.Error as exc:
        return [{"_catalog": abs_path, "_error": "sqlite_open_error:%s" % exc}]
    try:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        for t in tables:
            try:
                cur.execute("SELECT * FROM %s" % t)
            except sqlite3.Error as exc:
                out.append({"_catalog": abs_path, "_table": t,
                            "_error": "sqlite_query_error:%s" % exc})
                continue
            cols = [d[0] for d in cur.description]
            for row in cur.fetchall():
                out.append({"_catalog": abs_path, "_table": t,
                            **dict(zip(cols, row))})
    finally:
        con.close()
    return out


# ---------------------------------------------------------------------------
# Analysis stages (pure over IR)
# ---------------------------------------------------------------------------

def _analyze_duplicates(ir: Dict[str, Any]) -> Dict[str, Any]:
    groups: Dict[str, List[str]] = {}
    for e in ir["entries"]:
        if e["kind"] == "file" and e.get("sha256"):
            groups.setdefault(e["sha256"], []).append(e["path"])
    dup_groups = []
    for h, paths in sorted(groups.items()):
        if len(paths) > 1:
            dup_groups.append({"sha256": h, "paths": paths, "count": len(paths)})
    return {"groups": dup_groups, "total_groups": len(dup_groups),
            "duplicate_file_count": sum(g["count"] for g in dup_groups)}


def _version_stem(basename: str) -> str:
    stem = os.path.splitext(basename)[0]
    stem = VERSION_TOKENS.sub(" ", stem)
    stem = NON_ALNUM.sub(" ", stem)
    stem = re.sub(r"\s+", " ", stem).strip().lower()
    return stem


def _analyze_versions(ir: Dict[str, Any]) -> Dict[str, Any]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for e in ir["entries"]:
        if e["kind"] != "file":
            continue
        stem = _version_stem(e["basename"])
        if not stem:
            continue
        buckets.setdefault((e["parent"], stem), []).append(e)
    chains = []
    for (parent, stem), members in sorted(buckets.items()):
        if len(members) < 2:
            continue
        members_sorted = sorted(members, key=lambda m: (m.get("mtime") or 0, m["path"]))
        chain = []
        for i, m in enumerate(members_sorted):
            delta = None
            if i > 0:
                prev = members_sorted[i - 1]
                same = (m.get("sha256") == prev.get("sha256") and m.get("sha256") is not None)
                delta = {
                    "from": prev["path"], "to": m["path"],
                    "size_delta": (m.get("size") or 0) - (prev.get("size") or 0),
                    "same_content": same,
                    "from_hash_prefix": (prev.get("sha256") or "")[:12] or None,
                    "to_hash_prefix": (m.get("sha256") or "")[:12] or None,
                }
            chain.append({"path": m["path"], "sha256_prefix":
                          (m.get("sha256") or "")[:12] or None,
                          "mtime": m.get("mtime"), "size": m.get("size"),
                          "delta": delta})
        chains.append({"stem": stem, "parent": parent, "members": chain,
                       "heuristic": True,
                       "note": "candidate_chain_by_normalized_stem_and_mtime"})
    return {"chains": chains, "total_chains": len(chains),
            "method": "normalized_stem + content_hash + mtime (heuristic)"}


def _analyze_aliases(ir: Dict[str, Any]) -> Dict[str, Any]:
    sym_by_target: Dict[str, List[str]] = {}
    broken: List[str] = []
    for e in ir["entries"]:
        if e["kind"] in ("symlink", "broken_link"):
            if e.get("broken"):
                broken.append(e["path"])
            target = e.get("link_target") or "(unknown)"
            sym_by_target.setdefault(target, []).append(e["path"])
    hardlink_groups = []
    inode_map: Dict[Tuple[int, int], List[str]] = {}
    for e in ir["entries"]:
        if e.get("inode"):
            key = (e["inode"][0], e["inode"][1])
            inode_map.setdefault(key, []).append(e["path"])
    for key, paths in sorted(inode_map.items()):
        if len(paths) > 1:
            hardlink_groups.append({"dev": key[0], "ino": key[1],
                                    "paths": paths})
    return {
        "symlink_groups": [{"target": t, "paths": p}
                           for t, p in sorted(sym_by_target.items())],
        "broken_links": broken,
        "hardlink_groups": hardlink_groups,
        "hardlink_detection": "available" if sys.platform != "win32"
                              else "unavailable_on_windows",
    }


def _parse_version_notes(ir: Dict[str, Any], task_text: str,
                         case: Any) -> Dict[str, Any]:
    notes: List[Dict[str, Any]] = []
    # 1) Explicit fields in case.json.
    if isinstance(case, dict):
        for k in ("version_notes", "versions", "version_history",
                  "administrative_versions"):
            if isinstance(case.get(k), (list, dict, str)):
                notes.append({"source": "case.json:%s" % k,
                              "content": case[k]})
    # 2) Keyword-marked sections in task.md.
    if task_text:
        for m in re.finditer(
                r"(版本说明|版本记录|version[_ ]?notes?|版本关系|supersed\w*|"
                r"replac\w*)", task_text, re.IGNORECASE):
            ctx = task_text[max(0, m.start() - 80):m.start() + 400]
            notes.append({"source": "task.md",
                          "matched": m.group(0),
                          "context": ctx.strip()})
    # 3) Catalog files whose records declare replacement/version fields.
    for rec in ir.get("catalogs", {}).get("records", []):
        for f in ("replaces", "supersedes", "version", "revision", "parent",
                  "superseded_by", "revision_of"):
            if rec.get(f):
                notes.append({"source": rec.get("_catalog"),
                              "record": {k: v for k, v in rec.items()
                                         if not k.startswith("_")},
                              "field": f, "value": rec[f]})
                break
    return {"notes": notes, "count": len(notes),
            "aligned": "candidate_chains_marked_linked_when_members_overlap"}


def _declared_records(catalogs: Dict[str, Any]) -> List[Dict[str, Any]]:
    recs = catalogs.get("records", [])
    return [r for r in recs if not r.get("_error")]


def _record_path_keys(rec: Dict[str, Any]) -> List[str]:
    out = []
    for k in ("path", "file", "filename", "name", "relative_path", "filepath",
              "relpath", "id"):
        if rec.get(k):
            out.append(str(rec[k]).replace("\\", "/"))
    return out


def _analyze_reconciliation(ir: Dict[str, Any]) -> Dict[str, Any]:
    catalog = ir.get("catalogs", {})
    recs = _declared_records(catalog)
    if not recs:
        return {"applicable": False, "matches": [], "mismatches": [],
                "missing": [], "orphaned": [], "note":
                "no_catalog_manifest_found"}
    fs_paths = {e["path"] for e in ir["entries"] if e["kind"] == "file"}
    fs_by_name = {}
    for e in ir["entries"]:
        if e["kind"] == "file":
            fs_by_name.setdefault(e["basename"], []).append(e["path"])

    matches, mismatches, missing = [], [], []
    seen_paths = set()
    for rec in recs:
        keys = _record_path_keys(rec)
        hit = None
        for k in keys:
            if k in fs_paths:
                hit = k
                break
        if hit is None:
            for k in keys:
                base = k.split("/")[-1]
                if base in fs_by_name and len(fs_by_name[base]) == 1:
                    hit = fs_by_name[base][0]
                    break
        if hit is None:
            missing.append({"declared": keys[0] if keys else rec,
                            "record": {k: v for k, v in rec.items()
                                       if not k.startswith("_")}})
            continue
        seen_paths.add(hit)
        declared_size = rec.get("size")
        declared_hash = rec.get("sha256") or rec.get("sha256sum") or rec.get("hash")
        entry = next(e for e in ir["entries"] if e["path"] == hit)
        if declared_size not in (None, "", entry.get("size")) or \
           (declared_hash and declared_hash != entry.get("sha256")):
            mismatches.append({"path": hit,
                               "declared_size": declared_size,
                               "actual_size": entry.get("size"),
                               "declared_hash": declared_hash,
                               "actual_hash": entry.get("sha256"),
                               "record": {k: v for k, v in rec.items()
                                          if not k.startswith("_")}})
        else:
            matches.append({"path": hit,
                            "record": {k: v for k, v in rec.items()
                                       if not k.startswith("_")}})
    orphaned = sorted(fs_paths - seen_paths)
    return {"applicable": True, "matches": matches, "mismatches": mismatches,
            "missing": missing, "orphaned": orphaned,
            "counts": {"matches": len(matches), "mismatches": len(mismatches),
                       "missing": len(missing), "orphaned": len(orphaned)}}


def _build_index(ir: Dict[str, Any]) -> Dict[str, Any]:
    term_map: Dict[str, List[str]] = {}
    def add(term: str, path: str) -> None:
        if not term:
            return
        t = term.strip().lower()
        if t:
            term_map.setdefault(t, [])
            if path not in term_map[t]:
                term_map[t].append(path)
    for e in ir["entries"]:
        if e["kind"] != "file":
            continue
        add(e["basename"], e["path"])
        add(e["path"], e["path"])
        add(e.get("content_kind") or "", e["path"])
        for tok in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", e["basename"]):
            add(tok, e["path"])
    for rec in _declared_records(ir.get("catalogs", {})):
        for f in ("title", "author", "year", "journal", "doi", "project",
                  "instrument", "institution"):
            val = rec.get(f)
            if val:
                path = rec.get("_catalog", "(catalog)")
                if isinstance(val, list):
                    val = "; ".join(str(v) for v in val)
                for tok in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", str(val)):
                    add(tok, str(path))
        for k in _record_path_keys(rec):
            add(k.split("/")[-1], str(rec.get("_catalog", "(catalog)")))
    return {"terms": {k: sorted(v) for k, v in sorted(term_map.items())},
            "term_count": len(term_map)}


def _answer_questions(ir: Dict[str, Any]) -> Dict[str, Any]:
    files = [e for e in ir["entries"] if e["kind"] == "file"]
    dirs = [e for e in ir["entries"] if e["kind"] == "dir"]
    broken = ir["aliases"]["broken_links"]
    dups = ir["duplicates"]
    chains = ir["versions"]
    rec = ir["reconciliation"]
    answers = [
        {"question": "总文件数", "answer": len(files),
         "evidence": [{"locator": "inventory.ir.json", "quote":
                       "entries with kind=file"}]},
        {"question": "总目录数（含空目录）", "answer": len(dirs),
         "evidence": [{"locator": "inventory.ir.json", "quote":
                       "entries with kind=dir"}]},
        {"question": "内容重复组数", "answer": dups["total_groups"],
         "evidence": [{"locator": "dedup_version.json", "quote":
                       "duplicate_groups"}]},
        {"question": "断链/失效符号链接数", "answer": len(broken),
         "evidence": [{"locator": "dedup_version.json", "quote":
                       "broken_links"}]},
        {"question": "候选版本链数", "answer": chains["total_chains"],
         "evidence": [{"locator": "dedup_version.json", "quote":
                       "version_candidates"}]},
    ]
    if rec["applicable"]:
        c = rec["counts"]
        answers.append({"question": "对账结果（matches/mismatches/missing/"
                                    "orphaned）", "answer": c,
                        "evidence": [{"locator": "recovery_report.json",
                                      "quote": "reconciliation counts"}]})
    # Distinct metadata fields found across catalogs.
    recs = _declared_records(ir.get("catalogs", {}))
    authors = sorted({str(r.get("author")) for r in recs if r.get("author")})
    years = sorted({str(r.get("year")) for r in recs if r.get("year")})
    answers.append({"question": "目录清单中的作者", "answer": authors,
                    "evidence": [{"locator": "catalogs", "quote":
                                  "author fields from catalogs"}]})
    answers.append({"question": "目录清单中的年份", "answer": years,
                    "evidence": [{"locator": "catalogs", "quote":
                                  "year fields from catalogs"}]})
    return {"answers": answers, "count": len(answers)}


# ---------------------------------------------------------------------------
# Tree, artifacts, result
# ---------------------------------------------------------------------------

def _build_tree(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    root: Dict[str, Any] = {"name": "", "type": "root", "children": []}
    for e in entries:
        parts = [p for p in e["path"].split("/") if p]
        node = root
        for i, p in enumerate(parts):
            is_leaf = i == len(parts) - 1
            child = next((c for c in node["children"] if c["name"] == p), None)
            if child is None:
                child = {"name": p,
                         "type": e["kind"] if is_leaf else "dir",
                         "children": [],
                         "empty": True if (is_leaf and e["kind"] == "dir") else False}
                node["children"].append(child)
            node = child
        if is_leaf:
            node["kind"] = e["kind"]
            node["size"] = e.get("size")
            node["sha256"] = e.get("sha256")
            node["link_target"] = e.get("link_target")
    return root


def _render_tree(root: Dict[str, Any], prefix: str = "") -> str:
    lines: List[str] = []
    for i, child in enumerate(sorted(root["children"], key=lambda c: c["name"])):
        is_last = i == len(root["children"]) - 1
        connector = "`-- " if is_last else "|-- "
        label = child["name"]
        if child.get("type") == "dir" and not child.get("children"):
            label += " [空目录]"
        if child.get("kind") == "broken_link":
            label += " -> %s [断链]" % (child.get("link_target") or "?")
        elif child.get("kind") == "symlink":
            label += " -> %s" % (child.get("link_target") or "?")
        lines.append(prefix + connector + label)
        if child.get("children"):
            lines.append(_render_tree(child, prefix +
                                      ("    " if is_last else "|   ")))
    return "\n".join(lines)


def _csv_manifest(entries: List[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["path", "kind", "size", "mtime", "mode", "permissions",
                "sha256", "content_kind", "encoding", "extension", "flags",
                "link_target"])
    for e in entries:
        w.writerow([e["path"], e["kind"], e.get("size") or "", e.get("mtime") or "",
                    oct(e.get("mode") or 0), _rwx(e.get("mode") or 0),
                    e.get("sha256") or "", e.get("content_kind") or "",
                    e.get("encoding") or "", e.get("extension") or "",
                    ",".join(e.get("flags", [])), e.get("link_target") or ""])
    return buf.getvalue()


def _emit_artifacts(ir: Dict[str, Any], outdir: str) -> List[str]:
    os.makedirs(outdir, exist_ok=True)
    paths: List[str] = []

    manifest_csv = os.path.join(outdir, "library_manifest.csv")
    _atomic_write_text(manifest_csv, _csv_manifest(ir["entries"]))
    paths.append(manifest_csv)

    tree_txt = os.path.join(outdir, "directory_tree.txt")
    _atomic_write_text(tree_txt, _render_tree(ir["tree"], "") + "\n")
    paths.append(tree_txt)

    dedup = os.path.join(outdir, "dedup_version.json")
    _atomic_write_json(dedup, {
        "duplicate_groups": ir["duplicates"],
        "version_candidates": ir["versions"],
        "aliases": ir["aliases"],
        "version_notes": ir["version_notes"],
    })
    paths.append(dedup)

    recovery = os.path.join(outdir, "recovery_report.json")
    _atomic_write_json(recovery, ir["reconciliation"])
    paths.append(recovery)

    index = os.path.join(outdir, "library_index.json")
    _atomic_write_json(index, ir["index"])
    paths.append(index)

    op_log = os.path.join(outdir, "operation_log.jsonl")
    _atomic_write_text(op_log, "\n".join(
        json.dumps(e, ensure_ascii=False) for e in ir["change_log"]) + "\n")
    paths.append(op_log)

    md = os.path.join(outdir, "library_manifest.md")
    _atomic_write_text(md, _render_handoff(ir))
    paths.append(md)

    return paths


def _render_handoff(ir: Dict[str, Any]) -> str:
    d = ir["discovery"]
    dup = ir["duplicates"]
    ver = ir["versions"]
    ali = ir["aliases"]
    rec = ir["reconciliation"]
    lines = [
        "# 资料库交接报告",
        "",
        "- case_id: %s" % ir["meta"].get("case_id", ""),
        "- 生成时间: %s" % ir["meta"].get("finished_at", ""),
        "- schema: %s" % SCHEMA_VERSION,
        "",
        "## 概要",
        "- 条目总数: %d（文件 %d / 目录 %d / 符号链接 %d / 断链 %d / 其他 %d）" % (
            d["total_entries"], d["total_files"], d["total_dirs"],
            d["total_symlinks"], d["total_broken_links"], d["total_other"]),
        "- 总字节数: %d" % d.get("bytes", 0),
        "- 内容重复组: %d" % dup["total_groups"],
        "- 候选版本链: %d（启发式）" % ver["total_chains"],
        "- 符号链接组: %d；硬链接组: %d；断链: %d" % (
            len(ali["symlink_groups"]), len(ali["hardlink_groups"]),
            len(ali["broken_links"])),
        "",
        "## 对账",
    ]
    if rec["applicable"]:
        c = rec["counts"]
        lines.append("- matches=%d mismatches=%d missing=%d orphaned=%d" % (
            c["matches"], c["mismatches"], c["missing"], c["orphaned"]))
    else:
        lines.append("- 未发现可对账的目录/清单文件。")
    lines += [
        "",
        "## 降级与错误",
    ]
    for e in ir.get("errors", []):
        lines.append("- [error] %s" % e)
    for e in ir.get("degradations", []):
        lines.append("- [degradation] %s" % e)
    if not ir.get("errors") and not ir.get("degradations"):
        lines.append("- 无。")
    lines += [
        "",
        "本报告为机械产物，可与 library_manifest.csv / dedup_version.json / "
        "recovery_report.json / operation_log.jsonl 交叉核对。",
    ]
    return "\n".join(lines) + "\n"


def _build_result(ir: Dict[str, Any], contract: Optional[Any],
                  contract_keys: List[str], decisions: Dict[str, Any],
                  outdir: str, status: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "case_id": ir["meta"].get("case_id", ""),
        "status": status,
    }
    # Backfill contract keys first (original position, unknown keys kept).
    contract_values = decisions.get("contract", {}) if isinstance(decisions, dict) else {}
    for key in contract_keys:
        if key in RESERVED_KEYS:
            result.setdefault("computed", {})["_contract_" + key] = \
                contract_values.get(key, _contract_placeholder(contract, key))
        else:
            result[key] = contract_values.get(key,
                                              _contract_placeholder(contract, key))
    result["sources"] = ir.get("sources", [])
    result["uncertainty"] = ir.get("uncertainty", [])
    if not any(u.get("item") == "output_contract"
               for u in result["uncertainty"]) and not contract:
        result["uncertainty"].append({
            "item": "output_contract",
            "reason": "case.json/task.md 未提供显式 output_contract；"
                      "已按元数据字段生成合法 result.json",
            "severity": "low"})
    result["pending_human"] = ir.get("pending_human", [])
    result["change_log"] = ir["change_log"]
    result["errors"] = ir.get("errors", [])
    result["degradations"] = ir.get("degradations", [])
    result["limitations"] = ir.get("limitations", [])
    result["runtime"] = ir.get("runtime", {})
    result["computed"] = ir.get("computed", {})
    result["artifacts"] = [os.path.basename(p) for p in
                           ir.get("artifact_paths", [])]
    result["decisions_applied"] = bool(contract_values or
                                       decisions.get("notes"))
    return result


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------

def _discover_case(case_path: str, task_path: str) -> Tuple[Optional[Any], str, Optional[str]]:
    case = None
    task_text = ""
    case_error = None
    if os.path.isfile(case_path):
        try:
            with open(case_path, "rb") as fh:
                case = json.loads(fh.read().decode("utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            case_error = "case_json_error:%s" % exc
    else:
        case_error = "case_json_missing:%s" % case_path
    if os.path.isfile(task_path):
        try:
            with open(task_path, "rb") as fh:
                task_text = fh.read().decode("utf-8-sig")
        except OSError:
            task_text = ""
    return case, task_text, case_error


def _case_id(case: Any, task_text: str, case_path: str) -> str:
    if isinstance(case, dict):
        for k in ("case_id", "id", "challenge_id", "challengeId", "caseId"):
            if case.get(k):
                return str(case[k])
    m = re.search(r"challenge[_ -]?id[:：\s]*`?([A-Za-z0-9_-]+)`?", task_text)
    if m:
        return m.group(1)
    m = re.search(r"`(challenge-[A-Za-z0-9_-]+)`", task_text)
    if m:
        return m.group(1)
    return os.path.basename(os.path.dirname(case_path)) or "unknown"


def _status_for(ir: Dict[str, Any], contract: Any,
                decisions: Dict[str, Any]) -> str:
    if ir.get("input_error"):
        return "input_error"
    if ir["discovery"]["total_files"] == 0 and not ir.get("errors"):
        return "insufficient_evidence"
    if ir.get("errors") or ir.get("degradations"):
        return "partial"
    if contract and not decisions.get("contract"):
        # Contract exists but no semantic values supplied yet.
        return "partial"
    if ir.get("pending_human"):
        return "partial"
    return "ok"


def run_pipeline(case_path: str, task_path: str, input_root: str,
                 output_dir: str, work_dir: str, decisions_path: str,
                 skeleton_only: bool = False) -> int:
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    started = _now_iso()
    t0 = time.time()

    case, task_text, case_error = _discover_case(case_path, task_path)
    contract, contract_keys = _find_contract(case, task_text)
    case_id = _case_id(case, task_text, case_path)

    change_log = [{"time": started, "actor": "skill",
                   "step": "skeleton" if skeleton_only else "start",
                   "detail": "pipeline invoked; case_id=%s" % case_id}]

    # Skeleton mode: emit a valid partial result immediately and return.
    if skeleton_only:
        result_path = os.path.join(output_dir, "result.json")
        skeleton = {
            "case_id": case_id,
            "status": "input_error" if (case_error and
                                        case_error.startswith("case_json_missing"))
            else "partial",
        }
        for key in contract_keys:
            if key in RESERVED_KEYS:
                skeleton.setdefault("computed", {})["_contract_" + key] = \
                    _contract_placeholder(contract, key)
            else:
                skeleton[key] = _contract_placeholder(contract, key)
        skeleton["sources"] = []
        skeleton["uncertainty"] = [{
            "item": "skeleton", "reason": "early partial skeleton before scan",
            "severity": "low"}]
        skeleton["pending_human"] = []
        skeleton["change_log"] = change_log
        skeleton["errors"] = [case_error] if case_error else []
        skeleton["degradations"] = []
        skeleton["limitations"] = ["skeleton written before full scan"]
        skeleton["runtime"] = {"seconds": round(time.time() - t0, 3),
                               "python": platform.python_version(),
                               "platform": sys.platform}
        _atomic_write_json(result_path, skeleton)
        return 0

    # Decisions overlay (optional, agent-authored).
    decisions: Dict[str, Any] = {}
    if decisions_path and os.path.isfile(decisions_path):
        try:
            with open(decisions_path, "rb") as fh:
                decisions = json.loads(fh.read().decode("utf-8-sig"))
            if not isinstance(decisions, dict):
                decisions = {}
        except (OSError, json.JSONDecodeError) as exc:
            decisions = {"_error": "decisions_json_error:%s" % exc}

    # Discovery + normalization.
    entries, scan_errors, scan_degradations = _scan(input_root)
    files = [e for e in entries if e["kind"] == "file"]
    dirs = [e for e in entries if e["kind"] == "dir"]
    syms = [e for e in entries if e["kind"] == "symlink"]
    broken = [e for e in entries if e["kind"] == "broken_link"]
    others = [e for e in entries if e["kind"] not in
              ("file", "dir", "symlink", "broken_link")]
    total_bytes = sum(e.get("size") or 0 for e in files)

    # Parse catalogs from every catalog-looking file.
    catalog_records: List[Dict[str, Any]] = []
    catalog_errors: List[str] = []
    for e in files:
        if e.get("content_kind") in CATALOG_EXTENSIONS.values():
            recs, err = _parse_catalog(e, os.path.join(input_root, e["path"]),
                                       input_root)
            if err:
                catalog_errors.append("%s:%s" % (e["path"], err))
            catalog_records.extend(recs)

    ir: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "case_id": case_id, "started_at": started,
            "finished_at": None, "input_root": input_root,
            "output_dir": output_dir, "work_dir": work_dir,
            "python": platform.python_version(), "platform": sys.platform,
        },
        "contract": contract, "contract_keys": contract_keys,
        "discovery": {
            "total_entries": len(entries), "total_files": len(files),
            "total_dirs": len(dirs), "total_symlinks": len(syms),
            "total_broken_links": len(broken), "total_other": len(others),
            "bytes": total_bytes,
        },
        "entries": entries,
        "catalogs": {"records": catalog_records,
                     "errors": catalog_errors},
        "errors": scan_errors, "degradations": scan_degradations,
        "limitations": [], "pending_human": [],
        "sources": [], "uncertainty": [],
        "input_error": False,
    }
    if not os.path.isdir(input_root):
        ir["input_error"] = True
        ir["errors"].append("input_root_missing:%s" % input_root)
    if case_error:
        if case_error.startswith("case_json_missing"):
            ir["input_error"] = True
            ir["errors"].append(case_error)
        else:
            # Unparseable case.json degrades but task.md can still supply the id.
            ir["degradations"].append(case_error)
            ir["limitations"].append("case.json unreadable; case_id recovered "
                                     "from task.md when possible")

    # Analysis stages (pure over ir).
    ir["tree"] = _build_tree(entries)
    ir["duplicates"] = _analyze_duplicates(ir)
    ir["versions"] = _analyze_versions(ir)
    ir["aliases"] = _analyze_aliases(ir)
    ir["version_notes"] = _parse_version_notes(ir, task_text, case)
    ir["reconciliation"] = _analyze_reconciliation(ir)
    ir["index"] = _build_index(ir)
    ir["answers"] = _answer_questions(ir)

    # Mechanical sources for key conclusions.
    ir["sources"] = _mechanical_sources(ir)

    # Honest limitation notes for catalog parse failures and empty input.
    if catalog_errors:
        ir["limitations"].append(
            "catalog_parse_errors: " + "; ".join(catalog_errors[:10]))
    if not files and not ir["input_error"]:
        ir["pending_human"].append(
            "no regular files found; verify input material location")

    # change_log reflects actual stages run (assembled before emission so the
    # operation log artifact contains the full timeline).
    ir["change_log"] = change_log + [
        {"time": _now_iso(), "actor": "skill",
         "step": "analysis", "detail": "duplicates=%d version_chains=%d "
         "broken_links=%d reconcile_applicable=%s" % (
             ir["duplicates"]["total_groups"], ir["versions"]["total_chains"],
             len(ir["aliases"]["broken_links"]),
             ir["reconciliation"]["applicable"])},
    ]

    # Emission.
    artifact_paths = _emit_artifacts(ir, output_dir)
    ir["artifact_paths"] = artifact_paths
    ir["meta"]["finished_at"] = _now_iso()
    ir["runtime"] = {"seconds": round(time.time() - t0, 3),
                     "python": platform.python_version(),
                     "platform": sys.platform}
    ir["change_log"].append({"time": ir["meta"]["finished_at"], "actor":
                             "skill", "step": "emit",
                             "detail": "wrote %d artifacts" % len(artifact_paths)})

    # Write IR to work dir (canonical, re-runnable).
    _atomic_write_json(os.path.join(work_dir, "inventory.ir.json"), ir)
    _atomic_write_json(os.path.join(work_dir, "decisions.empty.json"),
                       {"contract": {k: _contract_placeholder(contract, k)
                                     for k in contract_keys},
                        "notes": [], "_instructions":
                        "Fill contract values + notes with locator/quote, "
                        "then re-run pipeline."})

    status = _status_for(ir, contract, decisions)
    result = _build_result(ir, contract, contract_keys, decisions,
                           output_dir, status)
    _atomic_write_json(os.path.join(output_dir, "result.json"), result)

    # Save a compact computed summary inside result for easy review.
    result["computed"] = {
        "discovery": ir["discovery"],
        "duplicates": ir["duplicates"],
        "versions": ir["versions"],
        "aliases": ir["aliases"],
        "version_notes": ir["version_notes"],
        "reconciliation": ir["reconciliation"],
        "index_summary": {"term_count": ir["index"]["term_count"]},
        "answers": ir["answers"],
    }
    _atomic_write_json(os.path.join(output_dir, "result.json"), result)

    return 0


def _mechanical_sources(ir: Dict[str, Any]) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    for g in ir["duplicates"]["groups"][:20]:
        sources.append({"item": "duplicate_group",
                        "locator": "dedup_version.json",
                        "quote": "sha256=%s paths=%s" % (g["sha256"],
                                                         g["paths"])})
    for c in ir["versions"]["chains"][:20]:
        sources.append({"item": "version_candidate",
                        "locator": "dedup_version.json",
                        "quote": "stem=%s members=%s" % (
                            c["stem"], [m["path"] for m in c["members"]])})
    for b in ir["aliases"]["broken_links"]:
        sources.append({"item": "broken_link", "locator": "directory_tree.txt",
                        "quote": b})
    if ir["reconciliation"]["applicable"]:
        sources.append({"item": "reconciliation_counts",
                        "locator": "recovery_report.json",
                        "quote": str(ir["reconciliation"]["counts"])})
    return sources


# ---------------------------------------------------------------------------
# Check / validation
# ---------------------------------------------------------------------------

def _check(result_path: str, case_path: str, task_path: str) -> int:
    problems: List[str] = []
    if not os.path.isfile(result_path):
        print("FAIL: result.json not found: %s" % result_path)
        return 1
    try:
        with open(result_path, "rb") as fh:
            result = json.loads(fh.read().decode("utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print("FAIL: result.json unreadable: %s" % exc)
        return 1

    if not result.get("case_id"):
        problems.append("case_id empty")
    if result.get("status") not in ("ok", "partial", "insufficient_evidence",
                                    "input_error"):
        problems.append("status invalid: %r" % result.get("status"))
    for field in ("sources", "uncertainty", "change_log", "errors",
                  "degradations", "limitations", "pending_human"):
        if field not in result:
            problems.append("missing field: %s" % field)
        elif not isinstance(result[field], list):
            problems.append("field not a list: %s" % field)
    if not isinstance(result.get("sources"), list):
        problems.append("sources missing")
    else:
        for s in result["sources"]:
            if not all(k in s for k in ("item", "locator", "quote")):
                problems.append("source missing locator/quote: %r" % s)
                break
    # Contract completeness against case.json.
    case = None
    task_text = ""
    if os.path.isfile(case_path):
        try:
            with open(case_path, "rb") as fh:
                case = json.loads(fh.read().decode("utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            pass
    if os.path.isfile(task_path):
        try:
            with open(task_path, "rb") as fh:
                task_text = fh.read().decode("utf-8-sig")
        except OSError:
            pass
    contract, contract_keys = _find_contract(case, task_text)
    for key in contract_keys:
        if key in RESERVED_KEYS:
            ok = "computed" in result and "_contract_" + key in result["computed"]
            where = "computed._contract_" + key
        else:
            ok = key in result
            where = key
        if not ok:
            problems.append("contract key missing: %s" % where)
    # Artifacts referenced.
    outdir = os.path.dirname(os.path.abspath(result_path))
    for a in result.get("artifacts", []):
        if not os.path.isfile(os.path.join(outdir, a)):
            problems.append("artifact missing on disk: %s" % a)

    if problems:
        print("FAIL")
        for p in problems:
            print("  - %s" % p)
        return 1
    print("PASS: status=%s case_id=%s contract_keys=%d artifacts=%d" % (
        result.get("status"), result.get("case_id"), len(contract_keys),
        len(result.get("artifacts", []))))
    return 0


# ---------------------------------------------------------------------------
# CLI entry (imported by main.py)
# ---------------------------------------------------------------------------

def _opt(argv: List[str], name: str, default: str) -> str:
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def cli_main(command: str, argv: List[str]) -> int:
    if command == "skeleton":
        return run_pipeline(
            case_path=_opt(argv, "--case-json", "/input/case.json"),
            task_path=_opt(argv, "--task-path", "/input/task.md"),
            input_root=_opt(argv, "--input-root", "/input"),
            output_dir=_opt(argv, "--output-dir", "/app/output"),
            work_dir=_opt(argv, "--work-dir", "/app/work"),
            decisions_path=_opt(argv, "--decisions", ""),
            skeleton_only=True)
    if command == "check":
        result_path = _opt(argv, "--result", "/app/output/result.json")
        return _check(result_path,
                      _opt(argv, "--case-json", "/input/case.json"),
                      _opt(argv, "--task-path", "/input/task.md"))
    return run_pipeline(
        case_path=_opt(argv, "--case-json", "/input/case.json"),
        task_path=_opt(argv, "--task-path", "/input/task.md"),
        input_root=_opt(argv, "--input-root", "/input"),
        output_dir=_opt(argv, "--output-dir", "/app/output"),
        work_dir=_opt(argv, "--work-dir", "/app/work"),
        decisions_path=_opt(argv, "--decisions", ""),
        skeleton_only=False)


if __name__ == "__main__":
    # Invoked directly (bypassing main.py) for debugging.
    raise SystemExit(cli_main(sys.argv[1] if len(sys.argv) > 1 else "run",
                              sys.argv[2:]))
