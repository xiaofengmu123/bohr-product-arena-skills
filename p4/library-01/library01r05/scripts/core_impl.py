#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py - document library repair pipeline (Python standard library only).

Deterministic, replayable pipeline for a research document library:
  inventory -> dedup -> version candidates -> aliases -> administrative version
  notes -> library-manifest parsing -> reconciliation -> searchable index ->
  mechanical QA -> contract alignment -> editable artifacts -> result.json.

Every phase writes honest partial state first; a later failure never leaves an
empty output. All file reads are non-mutating with respect to /input.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import io
import json
import os
import re
import stat as stat_mod
import sys
import time

try:
    import sqlite3  # noqa: F401  (stdlib; only used for sqlite manifests)
except ImportError:  # pragma: no cover - defensive
    sqlite3 = None

UTC = _dt.timezone.utc
HERE = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------- #
# Constants and configuration
# --------------------------------------------------------------------------- #
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

CONTRACT_KEYS = (
    "output_contract", "outputContract", "output", "result_schema",
    "resultSchema", "contract",
)

RESERVED_KEYS = frozenset({
    "case_id", "status", "sources", "uncertainty", "change_log", "errors",
    "degradations", "runtime", "limitations", "pending_human", "computed",
    "_contract_meta", "artifacts",
})

APPEND_KEYS = frozenset({
    "sources", "uncertainty", "change_log", "errors", "degradations",
    "pending_human", "limitations",
})

MAX_HASH_BYTES = int(os.environ.get("LIBRARY_HASH_LIMIT_BYTES", "536870912"))  # 512 MiB
MAX_TEXT_BYTES = int(os.environ.get("LIBRARY_TEXT_LIMIT_BYTES", "8388608"))    # 8 MiB
DEFAULT_INPUT = os.environ.get("LIBRARY_INPUT", "/input")
DEFAULT_OUTPUT = os.environ.get("LIBRARY_OUTPUT", "/app/output")
DEFAULT_WORK = os.environ.get("LIBRARY_WORK", "/app/work")

VERSION_SUFFIX_RE = re.compile(
    r"[-_\s]*(?:(?:v|ver|version)[-_]?\d+(?:\.\d+)*"
    r"|\d{4}[-_]\d{2}[-_]\d{2}"
    r"|final|draft|old|new|backup|bak|copy|rev\d+"
    r"|(?:\u526f\u672c|\u5907\u4efd|\u65e7|\u65b0|\u7ec8\u7a3f))"
    r"(?:[-_\s]+|\((\d+)\))?$",
    re.IGNORECASE,
)

MANIFEST_NAME_RE = re.compile(
    r"(manifest|library|inventory|index|catalog|registry|files|metadata)",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return _dt.datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def relpath_posix(path: str, base: str) -> str:
    try:
        rel = os.path.relpath(path, base)
    except ValueError:
        rel = path
    return rel.replace(os.sep, "/")


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def read_bytes_limited(path: str, limit: int) -> tuple:
    """Return (bytes, truncated_flag, error)."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return b"", False, str(e)
    trunc = size > limit
    try:
        with open(path, "rb") as f:
            data = f.read(limit if trunc else max(size, 0))
    except OSError as e:
        return b"", False, str(e)
    return data, trunc, None


def decode_text(data: bytes):
    """Best-effort text decode. Returns (text, encoding)."""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def read_text_file(path: str, limit: int = MAX_TEXT_BYTES):
    """Return (text, encoding, truncated, error)."""
    if not path or not os.path.exists(path):
        return "", None, False, "missing"
    data, trunc, err = read_bytes_limited(path, limit)
    if err:
        return "", None, False, err
    text, enc = decode_text(data)
    return text, enc, trunc, None


def load_json_loose(path: str):
    """Return (obj_or_None, error_or_None). Tolerant of BOM and bad encoding."""
    if not path or not os.path.exists(path):
        return None, "missing"
    data, _, err = read_bytes_limited(path, MAX_TEXT_BYTES)
    if err:
        return None, err
    text, _ = decode_text(data)
    try:
        return json.loads(text), None
    except Exception as e:  # noqa: BLE001 - record, don't crash
        return None, f"json_parse_error: {e}"


def write_json(path: str, obj, sort_keys: bool = False) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=sort_keys)
        f.write("\n")
    os.replace(tmp, path)


def write_text(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def log_entry(seq: int, step: str, detail: str, fields=None) -> dict:
    return {
        "seq": seq,
        "time": now_iso(),
        "actor": "scripts/core_impl.py",
        "step": step,
        "detail": detail,
        "fields_affected": list(fields or []),
    }


def normalize_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


# --------------------------------------------------------------------------- #
# Contract extraction and alignment (variant focus)
# --------------------------------------------------------------------------- #
def find_contract(node, path: str = "$"):
    """Recursively locate the output contract inside an arbitrary case object."""
    if isinstance(node, dict):
        for key in CONTRACT_KEYS:
            if key in node and node[key] not in (None, "", [], {}):
                return node[key], f"{path}.{key}"
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                found, p = find_contract(value, f"{path}.{key}")
                if found is not None:
                    return found, p
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            if isinstance(value, (dict, list)):
                found, p = find_contract(value, f"{path}[{idx}]")
                if found is not None:
                    return found, p
    return None, None


def _is_schema_like(d: dict) -> bool:
    markers = ("type", "properties", "required", "default", "description", "items")
    return any(m in d for m in markers)


def _field_type(spec) -> str:
    if isinstance(spec, dict):
        t = spec.get("type")
        if t:
            return str(t)
        if "properties" in spec:
            return "object"
        if "items" in spec:
            return "array"
        return "unknown"
    if spec is None:
        return "null"
    if isinstance(spec, bool):
        return "boolean"
    if isinstance(spec, int):
        return "integer"
    if isinstance(spec, float):
        return "number"
    if isinstance(spec, list):
        return "array"
    if isinstance(spec, str):
        return "string"
    return "unknown"


def _make_field(name, spec, required):
    default = None
    if spec is not None and not isinstance(spec, (dict, list)):
        default = spec
    elif isinstance(spec, dict) and "default" in spec:
        default = spec["default"]
    return {
        "field": name,
        "path": name,
        "required": bool(required),
        "type": _field_type(spec),
        "default": default,
    }


def flatten_contract(contract):
    """Return a list of top-level field descriptors from a JSON-Schema-like or
    plain dict/list contract. Nested plain objects are treated as whole-field
    values (resolved by field name), while a JSON Schema `properties` map is
    expanded into its named properties."""
    fields = []
    if isinstance(contract, dict):
        props = contract.get("properties")
        required = set(contract.get("required", [])) if isinstance(contract.get("required"), list) else set()
        if props and isinstance(props, dict):
            for name, spec in sorted(props.items()):
                fields.append(_make_field(name, spec, name in required))
        else:
            for name, spec in sorted(contract.items()):
                if name in ("type", "properties", "required", "default", "description", "items", "title"):
                    continue
                fields.append(_make_field(name, spec, True))
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                fields.append(_make_field(item, None, True))
            elif isinstance(item, dict):
                for name, spec in sorted(item.items()):
                    fields.append(_make_field(name, spec, True))
    return fields


FIELD_ALIASES = {
    "files": "files", "filelist": "files", "manifest": "files", "inventory": "files",
    "fileinventory": "files", "catalog": "files",
    "summary": "summary", "overview": "summary",
    "filecount": "file_count", "count": "file_count", "totalfiles": "file_count",
    "directorycount": "directory_count", "directories": "directory_count",
    "duplicates": "duplicate_groups", "duplicategroups": "duplicate_groups",
    "duplicatefiles": "duplicate_groups", "dedup": "duplicate_groups",
    "versions": "version_candidates", "versioncandidates": "version_candidates",
    "versionchain": "version_candidates", "versionrelations": "version_candidates",
    "versionnotes": "version_notes", "administrativenotes": "version_notes",
    "aliases": "aliases", "links": "aliases", "symlinks": "aliases",
    "reconciliation": "reconciliation", "recovery": "reconciliation",
    "reconciliationreport": "reconciliation",
    "index": "index", "searchindex": "index",
    "tree": "tree", "directorytree": "tree", "dirtree": "tree",
    "answers": "answers", "questions": "answers", "qa": "answers",
    "artifacts": "artifacts", "outputs": "artifacts",
}


def resolve_contract_value(field: str, computed: dict):
    """Best-effort mapping from a contract field name to a computed value."""
    key = normalize_key(field)
    target = FIELD_ALIASES.get(key)
    if target and target in computed:
        return computed[target]
    # Fallback: direct computed key match.
    for ck, cv in computed.items():
        if normalize_key(ck) == key:
            return cv
    return None


def align_contract(contract_fields, computed):
    """Resolve every contract field; move reserved-key collisions to computed."""
    resolved = []
    moved = {}
    for f in contract_fields:
        field = f["field"]
        value = resolve_contract_value(field, computed)
        is_reserved = field in RESERVED_KEYS
        entry = {
            "field": field,
            "path": f["path"],
            "required": f["required"],
            "type": f["type"],
            "default": f["default"],
            "moved": is_reserved,
            "value": value,
            "status": "filled" if value not in (None, "", []) else "empty",
        }
        if is_reserved:
            moved[field] = value
            entry["status"] = "moved"
        resolved.append(entry)
    meta = {
        "source_key": resolved and "output_contract" or None,
        "total_fields": len(resolved),
        "filled": sum(1 for r in resolved if r["status"] == "filled"),
        "empty": sum(1 for r in resolved if r["status"] == "empty"),
        "moved": sum(1 for r in resolved if r["status"] == "moved"),
        "fields": resolved,
    }
    return resolved, moved, meta


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #
def _file_kind(st_mode: int) -> str:
    if stat_mod.S_ISDIR(st_mode):
        return "directory"
    if stat_mod.S_ISLNK(st_mode):
        return "symlink"
    if stat_mod.S_ISREG(st_mode):
        return "regular"
    if stat_mod.S_ISFIFO(st_mode):
        return "fifo"
    if stat_mod.S_ISSOCK(st_mode):
        return "socket"
    if stat_mod.S_ISCHR(st_mode):
        return "char_device"
    if stat_mod.S_ISBLK(st_mode):
        return "block_device"
    return "other"


def _mode_string(st_mode: int) -> str:
    try:
        return stat_mod.filemode(st_mode)
    except (AttributeError, ValueError):
        return oct(stat_mod.S_IMODE(st_mode))


def inspect_file(path: str, rel: str) -> dict:
    rec = {
        "relpath": rel,
        "abs_path": os.path.abspath(path),
        "name": os.path.basename(path),
        "ext": os.path.splitext(path)[1].lower(),
        "kind": "regular",
        "size": None,
        "mode": None,
        "mode_octal": None,
        "mtime": None,
        "mtime_epoch": None,
        "uid": None,
        "gid": None,
        "nlink": None,
        "dev": None,
        "ino": None,
        "sha256": None,
        "status": "ok",
        "link_target": None,
        "broken": None,
        "encoding": None,
        "error": None,
    }
    if os.path.islink(path):
        try:
            target = os.readlink(path)
        except OSError as e:
            target, rec["error"] = None, str(e)
        rec.update(kind="symlink", link_target=target, broken=not os.path.exists(path),
                   status="symlink")
        return rec
    try:
        st = os.lstat(path)
    except OSError as e:
        rec.update(status="unreadable", error=str(e))
        return rec
    kind = _file_kind(st.st_mode)
    rec.update(
        kind=kind,
        size=st.st_size,
        mode=_mode_string(st.st_mode),
        mode_octal=oct(stat_mod.S_IMODE(st.st_mode)),
        mtime=_dt.datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        mtime_epoch=st.st_mtime,
        uid=st.st_uid,
        gid=st.st_gid,
        nlink=st.st_nlink,
        dev=st.st_dev,
        ino=st.st_ino,
    )
    if kind != "regular":
        rec.update(status="non_regular", sha256=None)
        return rec
    if st.st_size > MAX_HASH_BYTES:
        rec.update(status="too_large", sha256=None)
        return rec
    try:
        rec["sha256"] = sha256_file(path)
        rec["status"] = "ok"
    except OSError as e:
        rec.update(status="unreadable", sha256=None, error=str(e))
    return rec


def walk_inventory(root: str):
    files, dirs, symlinks, broken, errors, degradations = [], [], [], [], [], []
    hardlink_map = {}

    def onerror(err):
        errors.append({
            "path": getattr(err, "filename", None) or str(err),
            "error": str(err),
        })

    if not os.path.isdir(root):
        errors.append({"path": root, "error": "input_root_not_a_directory"})
        return {
            "files": files, "dirs": dirs, "symlinks": symlinks, "broken_links": broken,
            "hardlink_groups": [], "unreadable_count": 0, "errors": errors,
            "degradations": degradations,
        }

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=onerror, followlinks=False):
        dirnames.sort()
        filenames.sort()
        rel_dir = relpath_posix(dirpath, root)
        try:
            dst = os.lstat(dirpath)
            dirs.append({
                "relpath": rel_dir,
                "mode": _mode_string(dst.st_mode),
                "mode_octal": oct(stat_mod.S_IMODE(dst.st_mode)),
                "mtime": _dt.datetime.fromtimestamp(dst.st_mtime, tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "uid": dst.st_uid,
                "gid": dst.st_gid,
            })
        except OSError as e:
            errors.append({"path": dirpath, "error": str(e)})
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            rel = relpath_posix(p, root)
            rec = inspect_file(p, rel)
            files.append(rec)
            if rec["kind"] == "symlink":
                symlinks.append(rec)
                if rec["broken"]:
                    broken.append(rec)
            elif rec["nlink"] and rec["nlink"] > 1:
                key = (rec.get("dev"), rec.get("ino"))
                hardlink_map.setdefault(key, []).append(rel)

    hardlink_groups = [sorted(v) for v in hardlink_map.values() if len(v) > 1]
    hardlink_groups.sort()
    unreadable_count = sum(1 for f in files if f.get("status") in ("unreadable", "too_large", "non_regular"))
    return {
        "files": files,
        "dirs": dirs,
        "symlinks": symlinks,
        "broken_links": broken,
        "hardlink_groups": hardlink_groups,
        "unreadable_count": unreadable_count,
        "errors": errors,
        "degradations": degradations,
    }


# --------------------------------------------------------------------------- #
# Dedup and version candidates
# --------------------------------------------------------------------------- #
def detect_duplicates(files):
    groups = {}
    for f in files:
        if f.get("sha256") and f.get("kind") == "regular":
            groups.setdefault(f["sha256"], []).append(f["relpath"])
    return sorted(({"sha256": h, "paths": sorted(v), "count": len(v)} for h, v in groups.items() if len(v) > 1),
                  key=lambda g: g["sha256"])


def normalized_stem(name: str) -> str:
    stem, _ = os.path.splitext(name)
    return VERSION_SUFFIX_RE.sub("", stem).strip("-_ ") or stem


def detect_version_candidates(files):
    stem_map = {}
    for f in files:
        if f.get("kind") != "regular":
            continue
        stem = normalized_stem(f["name"])
        stem_map.setdefault(stem, []).append(f)
    candidates = []
    for stem in sorted(stem_map):
        members = stem_map[stem]
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda m: (m.get("mtime_epoch") or 0, m["relpath"]))
        hashes = {m["sha256"] for m in members if m.get("sha256")}
        candidates.append({
            "stem": stem,
            "members": [
                {
                    "relpath": m["relpath"],
                    "sha256": m.get("sha256"),
                    "size": m.get("size"),
                    "mtime": m.get("mtime"),
                    "status": m.get("status"),
                } for m in ordered
            ],
            "distinct_hashes": sorted(hashes),
            "content_identical": len(hashes) == 1,
            "heuristic": True,
            "basis": "normalized-stem + content-hash + mtime (heuristic, not authoritative)",
        })
    return candidates


# --------------------------------------------------------------------------- #
# Administrative version notes
# --------------------------------------------------------------------------- #
VERSION_NOTE_PATTERNS = [
    re.compile(r"(?P<from>[^\s=>]+)\s*(?:->|=>|→)\s*(?P<to>[^\s=]+)(?P<rest>.*)", re.IGNORECASE),
    re.compile(r"(?:replaces?|supersedes?|replaced[-_ ]by)\s*[:=]?\s*(?P<to>[^\s,;]+)", re.IGNORECASE),
    re.compile(r"version\s*[:=]?\s*(?P<ver>v?\d+(?:\.\d+)*)", re.IGNORECASE),
]

NOTE_FILE_RE = re.compile(r"(version|versions|note|notes|changelog|change[-_]log)", re.IGNORECASE)


def _note_quote(lines, idx, span=2):
    lo, hi = max(0, idx - span), min(len(lines), idx + span + 1)
    return "\n".join(lines[lo:hi]).strip()


def parse_version_notes(task_text: str, root: str, files):
    notes, degradations = [], []
    lines = (task_text or "").splitlines()
    for idx, line in enumerate(lines):
        if not re.search(r"版本|version|替代|replaces|supersede", line, re.IGNORECASE):
            continue
        m = VERSION_NOTE_PATTERNS[0].search(line)
        if m:
            notes.append({
                "source": "task.md",
                "locator": f"task.md#L{idx + 1}",
                "quote": line.strip(),
                "from": m.group("from").strip(),
                "to": m.group("to").strip(),
                "note": m.group("rest").strip(),
                "kind": "administrative_version_note",
            })
            continue
        notes.append({
            "source": "task.md",
            "locator": f"task.md#L{idx + 1}",
            "quote": line.strip(),
            "from": None,
            "to": None,
            "note": line.strip(),
            "kind": "administrative_version_note",
        })

    for f in files:
        if f.get("kind") != "regular":
            continue
        if not NOTE_FILE_RE.search(f["name"]):
            continue
        if f.get("size") and f["size"] > MAX_TEXT_BYTES:
            degradations.append({"path": f["relpath"], "reason": "note_file_too_large_to_parse"})
            continue
        abs_path = os.path.join(root, *f["relpath"].split("/")) if f["relpath"] else root
        text, enc, trunc, err = read_text_file(abs_path)
        if err:
            degradations.append({"path": f["relpath"], "reason": f"note_parse_error: {err}"})
            continue
        flines = text.splitlines()
        for j, fline in enumerate(flines):
            m = VERSION_NOTE_PATTERNS[0].search(fline)
            if m:
                notes.append({
                    "source": f["relpath"],
                    "locator": f"{f['relpath']}#L{j + 1}",
                    "quote": fline.strip(),
                    "from": m.group("from").strip(),
                    "to": m.group("to").strip(),
                    "note": m.group("rest").strip(),
                    "kind": "administrative_version_note",
                })
    return {"notes": notes, "degradations": degradations}


# --------------------------------------------------------------------------- #
# Library-manifest parsing (JSON / CSV / BibTeX / RIS / SQLite)
# --------------------------------------------------------------------------- #
def _as_entry(obj):
    """Coerce an arbitrary dict into an expected-file entry."""
    if not isinstance(obj, dict):
        return None
    path = (obj.get("relpath") or obj.get("path") or obj.get("file") or obj.get("filename")
            or obj.get("name") or obj.get("file_path") or obj.get("File"))
    if not path:
        return None
    entry = {
        "path": str(path).replace("\\", "/"),
        "sha256": obj.get("sha256") or obj.get("sha") or obj.get("checksum") or obj.get("hash"),
        "size": obj.get("size") or obj.get("bytes"),
        "metadata": {k: v for k, v in obj.items()
                     if k not in ("relpath", "path", "file", "filename", "name",
                                  "file_path", "File", "sha256", "sha", "checksum",
                                  "hash", "size", "bytes")},
    }
    return entry


def _extract_entries(obj, out):
    if isinstance(obj, dict):
        if any(k in obj for k in ("relpath", "path", "file", "filename", "name", "file_path")):
            e = _as_entry(obj)
            if e:
                out.append(e)
            return
        for v in obj.values():
            _extract_entries(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _extract_entries(v, out)


def parse_json_manifest(path: str):
    data, _, err = read_bytes_limited(path, MAX_TEXT_BYTES)
    if err:
        return {"kind": "json", "error": err, "entries": []}
    try:
        obj = json.loads(decode_text(data)[0])
    except Exception as e:  # noqa: BLE001
        return {"kind": "json", "error": f"json_parse_error: {e}", "entries": []}
    entries = []
    _extract_entries(obj, entries)
    return {"kind": "json", "entries": entries, "raw_count": len(entries)}


def parse_csv_manifest(path: str):
    data, _, err = read_bytes_limited(path, MAX_TEXT_BYTES)
    if err:
        return {"kind": "csv", "error": err, "entries": []}
    text = decode_text(data)[0]
    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
    except Exception as e:  # noqa: BLE001
        try:
            rows_raw = list(csv.reader(io.StringIO(text)))
            rows = [dict(zip(rows_raw[0], r)) for r in rows_raw[1:]]
        except Exception as e2:  # noqa: BLE001
            return {"kind": "csv", "error": f"csv_parse_error: {e2}", "entries": []}
    entries = [_as_entry(r) for r in rows]
    return {"kind": "csv", "entries": [e for e in entries if e], "raw_count": len(rows)}


def _bib_field(entry, key, aliases):
    for a in [key] + aliases:
        if a in entry:
            return entry[a]
    return None


def parse_bib(path: str):
    data, _, err = read_bytes_limited(path, MAX_TEXT_BYTES)
    if err:
        return {"kind": "bibtex", "error": err, "entries": []}
    text = decode_text(data)[0]
    entries = []
    # Simple, tolerant BibTeX entry parser.
    entry_re = re.compile(r"@(\w+)\s*\{\s*([^,]+),(.*?)\}", re.DOTALL)
    for m in entry_re.finditer(text):
        kind, key, body = m.group(1), m.group(2).strip(), m.group(3)
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*[{\"](.*?)[}\"]", body, re.DOTALL):
            fields[fm.group(1).lower()] = fm.group(2).strip()
        entries.append({
            "kind": kind,
            "key": key,
            "title": _bib_field(fields, "title", []),
            "author": _bib_field(fields, "author", []),
            "year": _bib_field(fields, "year", ["date"]),
            "doi": _bib_field(fields, "doi", []),
            "journal": _bib_field(fields, "journal", ["booktitle"]),
        })
    return {"kind": "bibtex", "entries": entries, "raw_count": len(entries)}


def parse_ris(path: str):
    data, _, err = read_bytes_limited(path, MAX_TEXT_BYTES)
    if err:
        return {"kind": "ris", "error": err, "entries": []}
    text = decode_text(data)[0]
    entries = []
    cur = {}
    for line in text.splitlines():
        line = line.rstrip("\r")
        if line.startswith("ER  -") or line.strip() == "":
            if cur:
                entries.append(cur)
                cur = {}
            continue
        if len(line) >= 6 and line[2:6] == "  - ":
            tag, val = line[:2].strip(), line[6:].strip()
            cur[tag] = (cur.get(tag) + "; " + val) if tag in cur else val
        elif line.startswith("TY  -"):
            cur["TY"] = line[6:].strip()
    if cur:
        entries.append(cur)
    norm = []
    for e in entries:
        norm.append({
            "kind": e.get("TY"),
            "title": e.get("TI"),
            "author": e.get("AU"),
            "year": e.get("PY") or e.get("Y1"),
            "doi": e.get("DO"),
            "journal": e.get("JO") or e.get("T2"),
        })
    return {"kind": "ris", "entries": norm, "raw_count": len(norm)}


def parse_sqlite(path: str):
    if sqlite3 is None:
        return {"kind": "sqlite", "error": "sqlite3_unavailable", "entries": []}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception as e:  # noqa: BLE001
        return {"kind": "sqlite", "error": f"sqlite_open_error: {e}", "entries": []}
    try:
        cur = conn.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        rows = []
        for t in tables:
            try:
                cols = [d[1] for d in cur.execute(f'PRAGMA table_info("{t}")').fetchall()]
                for row in cur.execute(f'SELECT * FROM "{t}"').fetchall():
                    rows.append(dict(zip(cols, row)))
            except Exception as e:  # noqa: BLE001
                rows.append({"__table__": t, "__error__": f"sqlite_query_error: {e}"})
        conn.close()
    except Exception as e:  # noqa: BLE001
        return {"kind": "sqlite", "error": f"sqlite_query_error: {e}", "entries": []}
    entries = [_as_entry(r) for r in rows]
    return {
        "kind": "sqlite",
        "tables": tables,
        "entries": [e for e in entries if e],
        "raw_count": len(rows),
    }


def parse_structured_manifests(root: str, files):
    parsed, degradations = [], []
    for f in files:
        if f.get("kind") != "regular":
            continue
        base = f["name"].lower()
        ext = f["ext"]
        is_manifest = MANIFEST_NAME_RE.search(base) or ext in (".bib", ".ris", ".sqlite", ".sqlite3", ".db")
        if not is_manifest:
            continue
        if f.get("status") == "too_large":
            degradations.append({"path": f["relpath"], "reason": "manifest_too_large"})
            continue
        abs_path = os.path.join(root, *f["relpath"].split("/")) if f["relpath"] else root
        if ext == ".json":
            result = parse_json_manifest(abs_path)
        elif ext == ".csv":
            result = parse_csv_manifest(abs_path)
        elif ext == ".bib":
            result = parse_bib(abs_path)
        elif ext == ".ris":
            result = parse_ris(abs_path)
        elif ext in (".sqlite", ".sqlite3", ".db"):
            result = parse_sqlite(abs_path)
        else:
            continue
        result["relpath"] = f["relpath"]
        result["sha256"] = f.get("sha256")
        if result.get("error"):
            degradations.append({"path": f["relpath"], "reason": result["error"]})
        parsed.append(result)
    return parsed, degradations


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #
def reconcile(expected_entries, files):
    by_relpath = {f["relpath"]: f for f in files}
    matches, mismatches, missing, orphaned = [], [], [], []
    seen_paths = set()
    for e in expected_entries:
        p = e["path"]
        seen_paths.add(p)
        f = by_relpath.get(p)
        if f is None:
            missing.append({"path": p, "expected_sha256": e.get("sha256"), "metadata": e.get("metadata")})
        elif e.get("sha256") and f.get("sha256") and e["sha256"].lower() != f["sha256"].lower():
            mismatches.append({"path": p, "expected_sha256": e["sha256"], "actual_sha256": f["sha256"]})
        else:
            matches.append({"path": p, "sha256": f.get("sha256"), "size": f.get("size")})
    for f in files:
        if f["relpath"] not in seen_paths:
            orphaned.append({"path": f["relpath"], "sha256": f.get("sha256"), "size": f.get("size")})
    for key in ("matches", "mismatches", "missing", "orphaned"):
        lst = {"matches": matches, "mismatches": mismatches,
               "missing": missing, "orphaned": orphaned}[key]
        lst.sort(key=lambda x: x.get("path", ""))
    return {
        "matches": matches,
        "mismatches": mismatches,
        "missing": missing,
        "orphaned": orphaned,
        "counts": {
            "matches": len(matches),
            "mismatches": len(mismatches),
            "missing": len(missing),
            "orphaned": len(orphaned),
        },
    }


# --------------------------------------------------------------------------- #
# Index and mechanical QA
# --------------------------------------------------------------------------- #
def build_index(files, parsed, reconciliation):
    by_ext = {}
    for f in files:
        by_ext.setdefault(f["ext"] or "(none)", []).append(f["relpath"])
    by_stem = {}
    for f in files:
        if f.get("kind") == "regular":
            by_stem.setdefault(normalized_stem(f["name"]), []).append(f["relpath"])
    authors, years, dois, titles = [], [], [], []
    for p in parsed:
        for e in p.get("entries", []):
            if e.get("author"):
                authors.append(e["author"])
            if e.get("year"):
                years.append(str(e["year"]))
            if e.get("doi"):
                dois.append(e["doi"])
            if e.get("title"):
                titles.append(e["title"])
    return {
        "by_extension": {k: sorted(v) for k, v in sorted(by_ext.items())},
        "by_stem": {k: sorted(v) for k, v in sorted(by_stem.items()) if len(v) > 1},
        "authors": sorted(set(authors)),
        "years": sorted(set(years)),
        "dois": sorted(set(dois)),
        "titles": sorted(set(titles)),
        "reconciliation_counts": reconciliation["counts"],
    }


def build_answers(inventory, duplicates, version_candidates, version_notes, reconciliation, index):
    files = inventory["files"]
    answers = [
        {
            "question": "输入目录中共有多少个文件？",
            "answer": len(files),
            "evidence": "computed.inventory.files",
        },
        {
            "question": "输入目录中共有多少个子目录？",
            "answer": len(inventory["dirs"]),
            "evidence": "computed.inventory.dirs",
        },
        {
            "question": "有多少组内容完全重复的文件（按 SHA-256）？",
            "answer": len(duplicates),
            "evidence": "computed.duplicate_groups",
        },
        {
            "question": "有多少组版本候选（按归一化主干启发式）？",
            "answer": len(version_candidates),
            "evidence": "computed.version_candidates",
        },
        {
            "question": "有多少个断链符号链接？",
            "answer": len(inventory["broken_links"]),
            "evidence": "computed.inventory.broken_links",
        },
        {
            "question": "对账中缺失（missing）的文件数？",
            "answer": reconciliation["counts"]["missing"],
            "evidence": "computed.reconciliation",
        },
        {
            "question": "对账中孤儿（orphaned）的文件数？",
            "answer": reconciliation["counts"]["orphaned"],
            "evidence": "computed.reconciliation",
        },
        {
            "question": "识别到的作者有哪些？",
            "answer": index["authors"],
            "evidence": "computed.index.authors",
        },
        {
            "question": "识别到的年份有哪些？",
            "answer": index["years"],
            "evidence": "computed.index.years",
        },
    ]
    return answers


# --------------------------------------------------------------------------- #
# Directory tree rendering
# --------------------------------------------------------------------------- #
def render_tree(inventory):
    files = inventory["files"]
    dirs = inventory["dirs"]
    dir_set = {d["relpath"] for d in dirs}
    child_map = {}
    for d in dir_set:
        if d == ".":
            continue
        parent = os.path.dirname(d)
        child_map.setdefault(parent if parent != "" else ".", []).append((d, "dir"))
    for f in files:
        parent = os.path.dirname(f["relpath"])
        child_map.setdefault(parent if parent != "" else ".", []).append((f["relpath"], f["kind"]))

    lines = ["."]

    def visit(node, prefix, is_last, is_root=False):
        children = sorted(set(child_map.get(node, [])))
        for i, (child, kind) in enumerate(children):
            last = i == len(children) - 1
            connector = "`-- " if last else "|-- "
            name = os.path.basename(child)
            tag = ""
            if kind == "symlink":
                tag = " -> (symlink)"
            elif kind != "dir":
                tag = ""
            lines.append(f"{prefix}{connector}{name}{tag}")
            if kind == "dir":
                visit(child, prefix + ("    " if last else "|   "), last)

    visit(".", "", True, is_root=True)
    # Mark empty dirs explicitly (a dir with no children registered in child_map).
    for d in sorted(dir_set):
        if d != "." and d not in child_map:
            lines.append(f"# [空目录] {d}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Result assembly
# --------------------------------------------------------------------------- #
def assemble_result(case_id, status, contract_fields, computed, moved, meta, sources,
                    uncertainty, pending_human, errors, degradations, limitations,
                    change_log, runtime):
    result = {"case_id": case_id, "status": status}
    for f in contract_fields:
        if f.get("moved"):
            continue
        result[f["field"]] = f.get("value")
    result["computed"] = computed
    for k, v in moved.items():
        result["computed"]["_contract_" + k] = v
    result["sources"] = sources
    result["uncertainty"] = uncertainty
    result["pending_human"] = pending_human
    result["change_log"] = change_log
    result["errors"] = errors
    result["degradations"] = degradations
    result["limitations"] = limitations
    result["runtime"] = runtime
    result["_contract_meta"] = meta
    return result


def _empty_contract_result(case_id, status, change_log, errors, runtime, contract_fields=None):
    fields = contract_fields or []
    moved = {f["field"]: f.get("value") for f in fields if f.get("moved")}
    meta = {
        "source_key": None, "total_fields": len(fields), "filled": 0,
        "empty": len(fields), "moved": len(moved), "fields": fields,
    }
    computed = {}
    return assemble_result(
        case_id, status, fields, computed, moved, meta,
        [], [], [], errors, [], [], change_log, runtime,
    )


# --------------------------------------------------------------------------- #
# Artifacts
# --------------------------------------------------------------------------- #
def write_artifacts(output_dir, inventory, duplicates, version_candidates, version_notes,
                    aliases, reconciliation, index, answers, parsed, contract_fields,
                    change_log):
    os.makedirs(output_dir, exist_ok=True)
    artifacts = {}

    # library_manifest.csv
    csv_path = os.path.join(output_dir, "library_manifest.csv")
    cols = ["relpath", "name", "ext", "kind", "size", "mode_octal", "mtime",
            "uid", "gid", "nlink", "sha256", "status", "link_target", "broken", "error"]
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for rec in inventory["files"]:
            w.writerow([rec.get(c, "") for c in cols])
    artifacts["library_manifest.csv"] = "one row per source file (hash, mode, mtime, status)"

    # directory_tree.txt
    tree_path = os.path.join(output_dir, "directory_tree.txt")
    write_text(tree_path, render_tree(inventory) + "\n")
    artifacts["directory_tree.txt"] = "directory tree, empty dirs marked [空目录]"

    # dedup_version.json
    dv_path = os.path.join(output_dir, "dedup_version.json")
    write_json(dv_path, {
        "duplicate_groups": duplicates,
        "version_candidates": version_candidates,
        "version_notes": version_notes,
        "aliases": aliases,
    })
    artifacts["dedup_version.json"] = "duplicates, version candidates, version notes, aliases"

    # recovery_report.json
    rr_path = os.path.join(output_dir, "recovery_report.json")
    write_json(rr_path, {
        "reconciliation": reconciliation,
        "parsed_manifests": parsed,
    })
    artifacts["recovery_report.json"] = "reconciliation matches/mismatches/missing/orphaned"

    # library_index.json
    idx_path = os.path.join(output_dir, "library_index.json")
    write_json(idx_path, index)
    artifacts["library_index.json"] = "searchable index by extension/stem/author/year/doi"

    # library_manifest.md
    md_path = os.path.join(output_dir, "library_manifest.md")
    md_lines = [
        "# 科研资料库交接报告",
        "",
        f"- case_id: {inventory.get('case_id', '(not available)')}",
        f"- 文件数: {len(inventory['files'])}",
        f"- 目录数: {len(inventory['dirs'])}",
        f"- 内容重复组: {len(duplicates)}",
        f"- 版本候选组: {len(version_candidates)}",
        f"- 断链符号链接: {len(inventory['broken_links'])}",
        f"- 对账: matches={reconciliation['counts']['matches']} "
        f"mismatches={reconciliation['counts']['mismatches']} "
        f"missing={reconciliation['counts']['missing']} "
        f"orphaned={reconciliation['counts']['orphaned']}",
        "",
        "## 复跑说明",
        "",
        "本报告由 `scripts/core_impl.py` 确定性生成；同一输入重复运行除时间戳外结果一致。",
        "去重按内容 SHA-256，版本候选按归一化主干 + 内容哈希 + 修改时间（启发式）。",
    ]
    write_text(md_path, "\n".join(md_lines) + "\n")
    artifacts["library_manifest.md"] = "handover report (editable)"

    # operation_log.jsonl
    log_path = os.path.join(output_dir, "operation_log.jsonl")
    with open(log_path, "w", encoding="utf-8", newline="\n") as fh:
        for entry in change_log:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    artifacts["operation_log.jsonl"] = "append-only operation log (replayable)"

    # contract_alignment.json (variant focus)
    ca_path = os.path.join(output_dir, "contract_alignment.json")
    write_json(ca_path, {
        "case_id": inventory.get("case_id"),
        "contract_fields": contract_fields,
    })
    artifacts["contract_alignment.json"] = "1:1 mapping of output_contract fields to result values"

    return artifacts


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
def run_pipeline(case_path, task_path, input_root, output_dir, work_dir, skeleton_only=False):
    start = time.time()
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    case_obj, case_err = load_json_loose(case_path)
    task_text, task_enc, task_trunc, task_err = read_text_file(task_path)
    case_id = resolve_case_id(case_obj, task_text)

    errors, degradations, limitations = [], [], []
    if case_err:
        errors.append({"component": "case.json", "error": case_err})
    if task_err and task_err != "missing":
        degradations.append({"component": "task.md", "reason": task_err})
    if task_trunc:
        degradations.append({"component": "task.md", "reason": "truncated_to_limit"})

    contract_obj, contract_path = find_contract(case_obj)
    contract_fields = flatten_contract(contract_obj) if contract_obj is not None else []
    if contract_obj is None:
        degradations.append({"component": "output_contract", "reason": "missing"})

    runtime = {
        "started_at": now_iso(),
        "seconds": 0.0,
        "python": sys.version.split()[0],
        "input_root": input_root,
    }
    change_log = [log_entry(0, "phase_0_skeleton", "pipeline started", ["case_id", "status"])]

    if skeleton_only:
        status = "input_error" if not case_id else "partial"
        result = _empty_contract_result(case_id, status, change_log, errors, runtime, contract_fields)
        write_json(os.path.join(output_dir, "result.json"), result)
        runtime["seconds"] = round(time.time() - start, 3)
        result["runtime"] = runtime
        write_json(os.path.join(output_dir, "result.json"), result)
        return 0

    # Phase 1: inventory + repair.
    inventory = walk_inventory(input_root)
    inventory["case_id"] = case_id
    errors.extend(inventory.pop("errors", []))
    degradations.extend(inventory.pop("degradations", []))

    duplicates = detect_duplicates(inventory["files"])
    version_candidates = detect_version_candidates(inventory["files"])
    version_notes = parse_version_notes(task_text, input_root, inventory["files"])
    degradations.extend(version_notes["degradations"])
    aliases = {
        "symlinks": inventory["symlinks"],
        "broken_symlinks": inventory["broken_links"],
        "hardlink_groups": inventory["hardlink_groups"],
    }
    parsed, manifest_degradations = parse_structured_manifests(input_root, inventory["files"])
    degradations.extend(manifest_degradations)
    expected_entries = []
    for p in parsed:
        expected_entries.extend(p.get("entries", []))
    reconciliation = reconcile(expected_entries, inventory["files"])
    index = build_index(inventory["files"], parsed, reconciliation)
    answers = build_answers(inventory, duplicates, version_candidates, version_notes,
                            reconciliation, index)

    computed = {
        "summary": {
            "file_count": len(inventory["files"]),
            "directory_count": len(inventory["dirs"]),
            "duplicate_group_count": len(duplicates),
            "version_candidate_count": len(version_candidates),
            "broken_symlink_count": len(inventory["broken_links"]),
            "hardlink_group_count": len(inventory["hardlink_groups"]),
            "reconciliation": reconciliation["counts"],
        },
        "files": inventory["files"],
        "inventory": {
            "dirs": inventory["dirs"],
            "symlinks": inventory["symlinks"],
            "broken_links": inventory["broken_links"],
            "hardlink_groups": inventory["hardlink_groups"],
        },
        "duplicate_groups": duplicates,
        "version_candidates": version_candidates,
        "version_notes": version_notes,
        "aliases": aliases,
        "reconciliation": reconciliation,
        "answers": answers,
        "index": index,
    }

    contract_fields, moved, contract_meta = align_contract(contract_fields, computed)
    contract_meta["source_path"] = contract_path or "(missing)"
    contract_meta["source_key"] = (contract_path or "output_contract").split(".")[-1]

    change_log.append(log_entry(1, "phase_1_inventory_repair",
                                f"inventory={len(inventory['files'])} files, "
                                f"{len(duplicates)} dup groups, "
                                f"{len(version_candidates)} version candidates, "
                                f"reconcile={reconciliation['counts']}",
                                ["computed", "status"]))
    change_log.append(log_entry(2, "phase_2_contract_alignment",
                                f"aligned {contract_meta['total_fields']} contract fields "
                                f"({contract_meta['filled']} filled, {contract_meta['moved']} moved)",
                                ["_contract_meta", "computed._contract_*"]))

    artifacts = write_artifacts(output_dir, inventory, duplicates, version_candidates,
                                version_notes, aliases, reconciliation, index, answers,
                                parsed, contract_fields, change_log)
    computed["artifacts"] = artifacts

    sources = build_sources(inventory, duplicates, version_candidates, reconciliation)
    uncertainty = build_uncertainty(contract_obj, version_candidates, reconciliation, inventory)
    pending_human = build_pending_human(reconciliation, inventory, contract_obj)

    # Honest status.
    material_files = [f for f in inventory["files"]
                      if f["relpath"] not in ("case.json", "task.md")]
    material_dirs = [d for d in inventory["dirs"] if d["relpath"] != "."]
    status = "ok"
    if not case_id:
        status = "input_error"
    elif contract_obj is None:
        status = "partial"
    elif len(material_files) == 0 and len(material_dirs) == 0:
        status = "insufficient_evidence"
    elif inventory.get("unreadable_count", 0) > 0 or errors:
        status = "partial"

    runtime["seconds"] = round(time.time() - start, 3)
    result = assemble_result(
        case_id, status, contract_fields, computed, moved, contract_meta,
        sources, uncertainty, pending_human, errors, degradations, limitations,
        change_log, runtime,
    )
    write_json(os.path.join(output_dir, "result.json"), result)

    # Persist work snapshot for incremental --update resume.
    write_json(os.path.join(work_dir, "inventory.json"), inventory)
    write_json(os.path.join(work_dir, "library.json"), {
        "computed": computed, "contract_fields": contract_fields,
        "duplicates": duplicates, "version_candidates": version_candidates,
        "reconciliation": reconciliation,
    })
    return 0


def resolve_case_id(case_obj, task_text):
    if isinstance(case_obj, dict):
        for key in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
            v = case_obj.get(key)
            if v:
                return str(v)
    if task_text:
        m = re.search(r"challenge_id[:：]\s*`?([A-Za-z0-9._-]+)", task_text)
        if m:
            return m.group(1)
        m = re.search(r"case[_ ]?id[:：]\s*`?([A-Za-z0-9._-]+)", task_text, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def build_sources(inventory, duplicates, version_candidates, reconciliation):
    sources = []
    for g in duplicates[:5]:
        sources.append({
            "item": f"duplicate group sha256={g['sha256'][:12]}...",
            "locator": "computed.duplicate_groups",
            "quote": json.dumps(g["paths"], ensure_ascii=False),
        })
    for v in version_candidates[:5]:
        sources.append({
            "item": f"version candidate stem={v['stem']}",
            "locator": "computed.version_candidates",
            "quote": json.dumps([m["relpath"] for m in v["members"]], ensure_ascii=False),
        })
    if reconciliation["mismatches"]:
        sources.append({
            "item": "reconciliation mismatch",
            "locator": "computed.reconciliation.mismatches",
            "quote": json.dumps(reconciliation["mismatches"][:5], ensure_ascii=False),
        })
    if not sources:
        sources.append({
            "item": "inventory",
            "locator": "computed.inventory",
            "quote": f"{len(inventory['files'])} files inventoried",
        })
    return sources


def build_uncertainty(contract_obj, version_candidates, reconciliation, inventory):
    u = []
    if contract_obj is None:
        u.append({"item": "output_contract", "reason": "contract absent from case.json",
                  "severity": "medium"})
    if version_candidates:
        u.append({"item": "version_candidates", "reason": "heuristic (normalized stem + hash + mtime); "
                  "requires semantic confirmation", "severity": "low"})
    if reconciliation["mismatches"]:
        u.append({"item": "reconciliation", "reason": f"{len(reconciliation['mismatches'])} hash mismatches",
                  "severity": "high"})
    if inventory.get("unreadable_count", 0):
        u.append({"item": "files", "reason": "some files unreadable/too_large; content not hashed",
                  "severity": "high"})
    return u


def build_pending_human(reconciliation, inventory, contract_obj):
    p = []
    if reconciliation["mismatches"]:
        p.append({"item": "hash mismatch", "action": "decide which copy is authoritative",
                  "evidence": "computed.reconciliation.mismatches"})
    if reconciliation["missing"]:
        p.append({"item": "missing files", "action": "locate or re-upload missing files",
                  "evidence": "computed.reconciliation.missing"})
    if contract_obj is None:
        p.append({"item": "output_contract", "action": "supply output_contract for full field alignment"})
    return p


# --------------------------------------------------------------------------- #
# --update and --check
# --------------------------------------------------------------------------- #
def deep_merge(base, patch):
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return patch
    out = dict(base)
    for k, v in patch.items():
        if k in APPEND_KEYS and isinstance(v, list):
            existing = out.get(k, [])
            merged = list(existing)
            seen = {json.dumps(x, ensure_ascii=False, sort_keys=True) for x in merged}
            for item in v:
                key = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if key not in seen:
                    merged.append(item)
                    seen.add(key)
            out[k] = merged
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def apply_update(patch_path, result_path):
    patch, err = load_json_loose(patch_path)
    if err:
        print(f"update: cannot load patch: {err}", file=sys.stderr)
        return 2
    result, err = load_json_loose(result_path)
    if err or not isinstance(result, dict):
        result = {"case_id": None, "status": "partial"}
    merged = deep_merge(result, patch)
    if "change_log" in merged and isinstance(merged["change_log"], list):
        seq = max((e.get("seq", -1) for e in merged["change_log"] if isinstance(e, dict)), default=-1) + 1
        detail = "semantic update applied"
        if isinstance(patch, dict):
            detail = "semantic update: " + ", ".join(sorted(patch.keys())[:8])
        merged["change_log"].append(log_entry(seq, "update", detail,
                                              list(patch.keys()) if isinstance(patch, dict) else None))
    write_json(result_path, merged)
    return 0


def check_result(result_path, case_path):
    result, err = load_json_loose(result_path)
    if err or not isinstance(result, dict):
        print(f"check: FAIL result not loadable: {err}")
        return 2
    problems = []
    if not result.get("case_id"):
        problems.append("case_id empty")
    if result.get("status") not in STATUS_VALUES:
        problems.append(f"status invalid: {result.get('status')!r}")
    if "change_log" not in result or not isinstance(result["change_log"], list):
        problems.append("change_log missing/not list")
    if "sources" not in result:
        problems.append("sources missing")
    if "uncertainty" not in result:
        problems.append("uncertainty missing")

    case_obj, _ = load_json_loose(case_path)
    contract_obj, _ = find_contract(case_obj)
    if contract_obj is not None:
        fields = flatten_contract(contract_obj)
        missing = []
        for f in fields:
            if f.get("moved"):
                target = result.get("computed", {}).get("_contract_" + f["field"], "__missing__")
                if target == "__missing__":
                    missing.append(f["field"] + " (moved)")
            elif f["field"] not in result:
                missing.append(f["field"])
        if missing:
            problems.append("contract fields missing: " + ", ".join(sorted(missing)))

    if problems:
        print("check: FAIL")
        for p in problems:
            print("  - " + p)
        return 2
    print(f"check: OK case_id={result.get('case_id')} status={result.get('status')} "
          f"contract_fields={len(result.get('_contract_meta', {}).get('fields', []))}")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_arg_parser():
    p = argparse.ArgumentParser(prog="main.py", add_help=False)
    p.add_argument("--case-json", default=os.path.join(DEFAULT_INPUT, "case.json"))
    p.add_argument("--task-path", default=os.path.join(DEFAULT_INPUT, "task.md"))
    p.add_argument("--input-root", default=DEFAULT_INPUT)
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    p.add_argument("--work-dir", default=DEFAULT_WORK)
    p.add_argument("--skeleton-only", action="store_true")
    p.add_argument("--update", metavar="PATCH.json")
    p.add_argument("--result", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    p.add_argument("--check", metavar="RESULT.json")
    p.add_argument("--help", action="store_true")
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or "--help" in argv or "-h" in argv:
        print("usage: main.py [--case-json PATH] [--task-path PATH] [--input-root DIR] "
              "[--output-dir DIR] [--work-dir DIR] [--skeleton-only] [--update PATCH] "
              "[--result PATH] [--check RESULT.json] [--help]")
        return 0
    args = build_arg_parser().parse_args(argv)

    if args.check:
        case_for_check = args.case_json
        return check_result(args.check, case_for_check)
    if args.update:
        return apply_update(args.update, args.result)

    return run_pipeline(
        case_path=args.case_json,
        task_path=args.task_path,
        input_root=args.input_root,
        output_dir=args.output_dir,
        work_dir=args.work_dir,
        skeleton_only=args.skeleton_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
