#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — deterministic document-library repair engine.

Responsibilities (standard library only):
  1. read-only inventory of /input (files, dirs, permissions, symlinks,
     hardlinks, content SHA-256, text previews);
  2. content-based deduplication;
  3. heuristic version-candidate chains (normalized stem + content hash +
     mtime) — explicitly labelled as heuristic;
  4. alias detection (symlinks incl. broken + hardlink groups);
  5. administrative version-note extraction;
  6. catalog parsing (JSON / CSV / BibTeX / RIS / SQLite) and
     matches / mismatches / missing / orphaned reconciliation;
  7. searchable index and mechanical query answers;
  8. honest result.json + editable artifacts, with an explicit
     degradation / uncertainty / limitation ledger and no fabrication.

All traversal is sorted and all hashing is deterministic, so repeated runs
over the same input are identical apart from wall-clock timestamps.
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
import sqlite3
import stat as _stat
import sys
import tempfile
import unicodedata
from collections import OrderedDict, defaultdict

VERSION = "2.0.0"
RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "runtime", "limitations",
    "computed", "artifacts",
}
STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
DEFAULT_MAX_BYTES = 2 * 1024 * 1024  # content-hash / preview cap
TEXT_EXTS = {
    ".txt", ".md", ".csv", ".json", ".jsonl", ".bib", ".ris", ".yaml",
    ".yml", ".xml", ".log", ".tex", ".py", ".rst", ".tsv", ".sql", ".ini",
    ".cfg", ".toml", ".html", ".htm",
}
CATALOG_EXTS = {".json", ".csv", ".tsv", ".bib", ".ris", ".sqlite", ".sqlite3", ".db"}


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------
def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def relpath_abs(root: str, path: str) -> str:
    """Deterministic relative locator using forward slashes."""
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        rel = path
    return rel.replace(os.sep, "/")


def log_event(log_path: str, actor: str, step: str, detail: str, start_time: str) -> None:
    """Append one JSON line to operation_log.jsonl."""
    rec = {
        "time": now_iso(),
        "elapsed_since_start": None,
        "actor": actor,
        "step": step,
        "detail": detail,
    }
    try:
        elapsed = (
            _dt.datetime.now(_dt.timezone.utc)
            - _dt.datetime.fromisoformat(start_time)
        ).total_seconds()
        rec["elapsed_since_start"] = round(elapsed, 3)
    except Exception:
        pass
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_text_safe(path: str, max_bytes: int = DEFAULT_MAX_BYTES):
    """Return (text, encoding, note). Never raises on decoding failures."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(max_bytes + 1)
    except OSError as exc:
        return None, None, "read_error:%s" % exc.__class__.__name__
    too_large = len(data) > max_bytes
    data = data[:max_bytes]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), enc, ("too_large" if too_large else None)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1", errors="replace"), "latin-1(replace)", "decode_fallback"


def hash_file(path: str, max_bytes: int = DEFAULT_MAX_BYTES):
    """Return (sha256hex_or_None, note). Skips oversized / non-regular files."""
    try:
        st = os.stat(path)
    except OSError as exc:
        return None, "stat_error:%s" % exc.__class__.__name__
    if not _stat.S_ISREG(st.st_mode):
        return None, "non_regular"
    if st.st_size > max_bytes:
        return None, "too_large"
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError as exc:
        return None, "read_error:%s" % exc.__class__.__name__
    return h.hexdigest(), None


def strip_version_tokens(name: str) -> str:
    """Normalize a stem for *heuristic* version grouping.

    Removes extension, version markers (v1.2, _v2, final, draft, copy, 副本,
    备份, 定稿, 修改, 旧, 新, rev, revised), ISO dates and 8-digit dates,
    then collapses whitespace. This is an approximation, not ground truth.
    """
    stem = os.path.splitext(name)[0]
    stem = unicodedata.normalize("NFKC", stem)
    patterns = [
        r"(?i)(?:^|[_\-\s.])(?:v|ver|version)?[_\-\s.]?\d+(?:[._-]\d+){0,3}",
        r"(?i)(?:^|[_\-\s.])(?:final|draft|copy|backup|old|new|latest|rev|revised|updated)",
        r"(?i)(?:^|[_\-\s.])(?:副本|备份|定稿|终稿|初稿|修改|修订|旧|新|最新)",
        r"(?:_|-|\.)?\d{4}-\d{2}-\d{2}",
        r"(?:_|-|\.)?\d{8}",
        r"(?:_|-|\.)?\d{4}\d{2}\d{2}",
    ]
    s = stem
    for pat in patterns:
        s = re.sub(pat, "", s)
    s = re.sub(r"[\s_\-\.]+", " ", s).strip()
    return s or stem


# --------------------------------------------------------------------------
# inventory
# --------------------------------------------------------------------------
def _dir_listing_safe(full: str):
    try:
        names = os.listdir(full)
        return names, None
    except OSError as exc:
        return [], exc.__class__.__name__


def make_entry(root: str, rel: str):
    full = os.path.join(root, rel)
    entry = {
        "path": rel,
        "name": os.path.basename(rel) if rel else rel,
        "size": None,
        "mtime": None,
        "mtime_epoch": None,
        "mode": None,
        "type": None,
    }
    try:
        lst = os.lstat(full)
        entry["size"] = lst.st_size
        entry["mtime"] = _dt.datetime.fromtimestamp(
            lst.st_mtime, _dt.timezone.utc
        ).isoformat(timespec="seconds")
        entry["mtime_epoch"] = lst.st_mtime
        entry["mode"] = format(_stat.S_IMODE(lst.st_mode), "o")
        entry["inode"] = getattr(lst, "st_ino", None)
        entry["nlink"] = getattr(lst, "st_nlink", None)
    except OSError as exc:
        entry["type"] = "unstatable"
        entry["error"] = exc.__class__.__name__
        return entry

    if os.path.islink(full):
        entry["type"] = "symlink"
        try:
            entry["target"] = os.readlink(full)
        except OSError as exc:
            entry["target"] = None
            entry["error"] = "readlink_error:%s" % exc.__class__.__name__
        try:
            os.stat(full)
            entry["link_broken"] = False
        except OSError:
            entry["link_broken"] = True
        return entry

    if _stat.S_ISDIR(lst.st_mode):
        entry["type"] = "dir"
        names, err = _dir_listing_safe(full)
        entry["empty"] = len(names) == 0
        if err:
            entry["error"] = "listdir_error:%s" % err
        return entry

    if _stat.S_ISREG(lst.st_mode):
        entry["type"] = "file"
        entry["readable"] = os.access(full, os.R_OK)
        entry["writable"] = os.access(full, os.W_OK)
        digest, note = hash_file(full, DEFAULT_MAX_BYTES)
        entry["sha256"] = digest
        if note:
            entry["hash_note"] = note
        ext = os.path.splitext(rel)[1].lower()
        if ext in TEXT_EXTS or entry["readable"]:
            text, enc, err = read_text_safe(full, DEFAULT_MAX_BYTES)
            if text is not None:
                entry["encoding"] = enc
                entry["preview"] = text[:200]
                if err:
                    entry["read_note"] = err
        return entry

    entry["type"] = "other"
    return entry


def build_inventory(root: str):
    """Deterministic walk. Returns (entries, files, dirs, symlinks)."""
    entries = []
    files = []
    dirs = []
    symlinks = []
    if not os.path.isdir(root):
        return entries, files, dirs, symlinks
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames.sort()
        filenames.sort()
        rel_dir = relpath_abs(root, dirpath)
        if rel_dir != ".":
            e = make_entry(root, rel_dir)
            entries.append(e)
            dirs.append(e)
        for name in filenames:
            rel = relpath_abs(root, os.path.join(dirpath, name))
            e = make_entry(root, rel)
            entries.append(e)
            if e["type"] == "file":
                files.append(e)
            elif e["type"] == "symlink":
                symlinks.append(e)
        # symlinked directories are listed in dirnames but not descended
        for name in list(dirnames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                rel = relpath_abs(root, full)
                e = make_entry(root, rel)
                entries.append(e)
                symlinks.append(e)
                dirnames.remove(name)
    return entries, files, dirs, symlinks


def build_tree(entries):
    """Produce a stable directory tree text (empty dirs marked)."""
    by_dir = defaultdict(list)
    dir_entries = {}
    for e in entries:
        if e["type"] == "dir":
            dir_entries[e["path"]] = e
        parent = os.path.dirname(e["path"])
        if parent == "":
            parent = "."
        by_dir[parent].append(e)
    for lst in by_dir.values():
        lst.sort(key=lambda x: (x["type"], x["path"]))
    lines = ["."]

    def render(parent, prefix):
        for e in by_dir.get(parent, []):
            is_last = (e is by_dir[parent][-1])
            branch = "└── " if is_last else "├── "
            if e["type"] == "dir":
                marker = " [空目录]" if e.get("empty") else ""
                lines.append(prefix + branch + e["name"] + "/" + marker)
                render(e["path"], prefix + ("    " if is_last else "│   "))
            elif e["type"] == "symlink":
                target = e.get("target")
                broken = " [断链]" if e.get("link_broken") else ""
                lines.append(prefix + branch + e["name"] + " -> " + str(target) + broken)
            else:
                note = ""
                if e.get("hash_note"):
                    note = " [%s]" % e["hash_note"]
                lines.append(prefix + branch + e["name"] + note)

    render(".", "")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# dedup / version / alias
# --------------------------------------------------------------------------
def build_duplicates(files):
    groups = defaultdict(list)
    for f in files:
        if f.get("sha256"):
            groups[f["sha256"]].append(f["path"])
    out = []
    for digest, paths in sorted(groups.items()):
        if len(paths) > 1:
            out.append({
                "sha256": digest,
                "count": len(paths),
                "paths": sorted(paths),
            })
    return out


def build_version_candidates(files):
    groups = defaultdict(list)
    for f in files:
        if f.get("sha256") is None:
            continue
        stem = strip_version_tokens(f["name"])
        if not stem:
            continue
        groups[stem.lower()].append(f)
    out = []
    for stem, members in sorted(groups.items()):
        hashes = {m["sha256"] for m in members if m.get("sha256")}
        if len(members) < 2 or len(hashes) < 2:
            continue
        ordered = sorted(members, key=lambda m: (m.get("mtime_epoch") or 0, m["path"]))
        out.append({
            "stem": stem,
            "method": "normalized_stem + content_hash + mtime (heuristic)",
            "count": len(members),
            "members": [
                {
                    "path": m["path"],
                    "sha256": m.get("sha256"),
                    "mtime": m.get("mtime"),
                    "size": m.get("size"),
                }
                for m in ordered
            ],
            "oldest": ordered[0]["path"],
            "newest": ordered[-1]["path"],
        })
    return out


def build_aliases(entries, symlinks):
    alias_groups = []
    for s in symlinks:
        alias_groups.append({
            "kind": "symlink",
            "path": s["path"],
            "target": s.get("target"),
            "broken": bool(s.get("link_broken")),
        })
    # hardlink groups by (inode) where nlink > 1
    hard = defaultdict(list)
    for e in entries:
        if e["type"] == "file" and e.get("inode") and (e.get("nlink") or 0) > 1:
            hard[e["inode"]].append(e["path"])
    for inode, paths in sorted(hard.items(), key=lambda kv: str(kv[0])):
        if len(paths) > 1:
            alias_groups.append({
                "kind": "hardlink",
                "inode": inode,
                "count": len(paths),
                "paths": sorted(paths),
            })
    return alias_groups


# --------------------------------------------------------------------------
# version notes + catalog parsing
# --------------------------------------------------------------------------
_VERSION_NOTE_RE = re.compile(
    r"(?i)(版本|version|修订|revised|rev\.|changelog|改动|变更|v\d+(\.\d+)*)",
)


def extract_version_notes(root, files, task_text=None):
    notes = []
    if task_text:
        for i, line in enumerate(task_text.splitlines(), 1):
            if _VERSION_NOTE_RE.search(line):
                notes.append({
                    "source": "task.md",
                    "line": i,
                    "text": line.strip(),
                })
    for f in files:
        text, _enc, _err = read_text_safe(os.path.join(root, f["path"]), 64 * 1024)
        if not text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _VERSION_NOTE_RE.search(line):
                notes.append({
                    "source": f["path"],
                    "line": i,
                    "text": line.strip(),
                })
                if len(notes) > 500:
                    return notes
    return notes


def _load_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def _load_csv(path):
    rows = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(fh, dialect=dialect)
        for row in reader:
            rows.append({k: (v if v is not None else "") for k, v in row.items()})
    return rows


def _load_tsv(path):
    rows = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            rows.append(dict(row))
    return rows


def _load_bibtex(path):
    text, _enc, _err = read_text_safe(path, 8 * 1024 * 1024)
    if not text:
        return []
    entries = []
    entry_re = re.compile(
        r"@(\w+)\s*\{\s*([^,]+)\s*,\s*(.*?)\s*\}",
        re.DOTALL,
    )
    field_re = re.compile(r"(\w+)\s*=\s*[{\"]([^{}\"]*)[}\"]", re.DOTALL)
    for m in entry_re.finditer(text):
        etype, key, body = m.group(1), m.group(2).strip(), m.group(3)
        fields = {}
        for fm in field_re.finditer(body):
            fields[fm.group(1).lower()] = fm.group(2).strip()
        fields["_type"] = etype
        fields["_key"] = key
        entries.append(fields)
    return entries


def _load_ris(path):
    text, _enc, _err = read_text_safe(path, 8 * 1024 * 1024)
    if not text:
        return []
    entries = []
    cur = OrderedDict()
    for line in text.splitlines():
        line = line.rstrip("\n")
        if not line.strip():
            continue
        if line.startswith("TY  -"):
            cur = OrderedDict([("TY", line[6:].strip())])
        elif line.startswith("ER  -"):
            entries.append(dict(cur))
            cur = OrderedDict()
        elif len(line) >= 6 and line[2:6] == "  - ":
            key = line[:2].strip()
            val = line[6:].strip()
            if key in cur:
                cur[key] = str(cur[key]) + " ; " + val
            else:
                cur[key] = val
    if cur:
        entries.append(dict(cur))
    return entries


def _load_sqlite(path):
    con = sqlite3.connect(path)
    try:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        out = {"tables": {}}
        for t in tables:
            try:
                rows = [dict(r) for r in cur.execute(
                    'SELECT * FROM "%s" LIMIT 1000' % t.replace('"', '""')
                ).fetchall()]
                out["tables"][t] = rows
            except sqlite3.Error as exc:
                out["tables"][t] = {"_query_error": str(exc)}
        return out
    except sqlite3.Error as exc:
        return {"_open_error": str(exc)}
    finally:
        try:
            con.close()
        except Exception:
            pass


def parse_catalogs(root, files):
    catalogs = []
    for f in files:
        ext = os.path.splitext(f["path"])[1].lower()
        if ext not in CATALOG_EXTS:
            continue
        full = os.path.join(root, f["path"])
        rec = {
            "path": f["path"],
            "format": ext.lstrip("."),
            "status": "ok",
            "data": None,
            "error": None,
        }
        try:
            if ext == ".json":
                rec["data"] = _load_json(full)
            elif ext == ".csv":
                rec["data"] = _load_csv(full)
            elif ext == ".tsv":
                rec["data"] = _load_tsv(full)
            elif ext == ".bib":
                rec["data"] = _load_bibtex(full)
            elif ext == ".ris":
                rec["data"] = _load_ris(full)
            elif ext in (".sqlite", ".sqlite3", ".db"):
                rec["data"] = _load_sqlite(full)
        except Exception as exc:  # noqa: BLE001 — record, never crash
            rec["status"] = "error"
            rec["error"] = "%s:%s" % (exc.__class__.__name__, str(exc)[:200])
        catalogs.append(rec)
    return catalogs


def _iter_dicts(obj):
    """Yield every dict nested anywhere in a catalog payload."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_dicts(v)


def _catalog_paths(catalog):
    """Extract candidate file references (path/filename-like) from a catalog."""
    paths = []
    data = catalog.get("data")
    seen = set()
    for d in _iter_dicts(data):
        for key, val in d.items():
            if key.startswith("_") or not isinstance(val, str) or not val:
                continue
            low = key.lower()
            if low in {"path", "file", "filename", "filepath", "location", "url", "source"} \
                    or low.endswith(("path", "file", "filename", "location")):
                if val not in seen:
                    seen.add(val)
                    paths.append(val)
    return paths


def build_reconciliation(catalogs, files):
    """Compare catalog-referenced files against actual inventory.

    Returns matches / mismatches / missing / orphaned.
    'mismatches' means the referenced name appears but resolves to a path we
    cannot map exactly (e.g. only a basename or an absent relative path).
    """
    file_paths = {f["path"] for f in files}
    file_basenames = defaultdict(list)
    for f in files:
        file_basenames[os.path.basename(f["path"])].append(f["path"])
    matches, mismatches, missing, orphaned = [], [], [], []
    seen = set()
    for cat in catalogs:
        if cat.get("error"):
            continue
        for ref in _catalog_paths(cat):
            if not ref:
                continue
            key = (cat["path"], ref)
            if key in seen:
                continue
            seen.add(key)
            norm = ref.replace("\\", "/")
            if norm in file_paths:
                matches.append({"catalog": cat["path"], "reference": ref, "path": norm})
            elif os.path.basename(norm) in file_basenames:
                for p in file_basenames[os.path.basename(norm)]:
                    mismatches.append({
                        "catalog": cat["path"],
                        "reference": ref,
                        "resolved_basename": p,
                        "reason": "basename_only",
                    })
            else:
                missing.append({"catalog": cat["path"], "reference": ref})
    orphaned = sorted(file_paths - {m["path"] for m in matches} - {m["resolved_basename"] for m in mismatches})
    return {
        "matches": matches,
        "mismatches": mismatches,
        "missing": missing,
        "orphaned": [{"path": p} for p in orphaned],
        "summary": {
            "matches": len(matches),
            "mismatches": len(mismatches),
            "missing": len(missing),
            "orphaned": len(orphaned),
        },
    }


# --------------------------------------------------------------------------
# index + mechanical answers
# --------------------------------------------------------------------------
def build_index(entries):
    index = []
    for e in entries:
        item = {
            "path": e["path"],
            "name": e["name"],
            "type": e["type"],
            "size": e.get("size"),
            "mtime": e.get("mtime"),
            "mode": e.get("mode"),
            "sha256": e.get("sha256"),
        }
        tokens = set()
        for part in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", e["name"]):
            if part:
                tokens.add(part.lower())
        item["keywords"] = sorted(tokens)
        index.append(item)
    index.sort(key=lambda x: x["path"])
    return index


def answer_mechanical(entries, files, dup_groups, version_candidates, aliases, reconciliation):
    """Return deterministic answers to common mechanical queries."""
    broken = [a for a in aliases if a.get("kind") == "symlink" and a.get("broken")]
    empty_dirs = [e["path"] for e in entries if e["type"] == "dir" and e.get("empty")]
    answers = {
        "total_entries": len(entries),
        "total_files": len(files),
        "total_directories": len([e for e in entries if e["type"] == "dir"]),
        "total_symlinks": len([e for e in entries if e["type"] == "symlink"]),
        "duplicate_groups": len(dup_groups),
        "duplicate_files": sum(g["count"] for g in dup_groups),
        "version_candidate_groups": len(version_candidates),
        "broken_symlinks": broken,
        "empty_directories": empty_dirs,
        "reconciliation_summary": reconciliation["summary"],
    }
    return answers


# --------------------------------------------------------------------------
# contract + result
# --------------------------------------------------------------------------
def load_case(case_path):
    if not os.path.isfile(case_path):
        return None, "missing_case_json"
    try:
        with open(case_path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001
        return None, "%s:%s" % (exc.__class__.__name__, str(exc)[:200])


def resolve_contract(case):
    """Locate output_contract under common aliases and normalize to a dict.

    Accepts both dict-style schemas ({field: schema}) and list-style field
    name arrays ([field, ...]).
    """
    if not isinstance(case, dict):
        return {}, None
    for key in ("output_contract", "output", "result_schema", "contract", "schema"):
        if key in case:
            val = case[key]
            if isinstance(val, dict):
                return val, key
            if isinstance(val, list):
                return {str(n): None for n in val}, key
    return {}, None


def build_result(case, contract, computed, artifacts, sources=None,
                 uncertainty=None, limitations=None, status="partial",
                 errors=None, degradations=None, pending_human=None):
    case_id = ""
    if isinstance(case, dict):
        case_id = str(
            case.get("case_id") or case.get("id") or case.get("challenge_id")
            or case.get("challengeId") or ""
        )
    result = OrderedDict()
    result["case_id"] = case_id
    result["status"] = status if status in STATUSES else "partial"
    # backfill contract fields verbatim; reserve-name conflicts go to computed
    for field, spec in contract.items():
        if field in RESERVED_KEYS:
            computed["_contract_" + field] = _contract_default(spec)
            continue
        result[field] = _contract_default(spec)
    result["sources"] = sources or []
    result["uncertainty"] = uncertainty or []
    result["pending_human"] = pending_human or []
    result["change_log"] = []
    result["errors"] = errors or []
    result["degradations"] = degradations or []
    result["runtime"] = {"engine_version": VERSION}
    result["limitations"] = limitations or []
    result["computed"] = computed
    result["artifacts"] = artifacts
    return result


def _contract_default(spec):
    """Fill a contract field with its declared default or a null placeholder."""
    if isinstance(spec, dict):
        if "default" in spec:
            return spec["default"]
        t = spec.get("type")
        if t == "array":
            return []
        if t == "object":
            return {}
        if t == "string":
            return ""
        if t in ("number", "integer", "boolean"):
            return None
        return None
    if isinstance(spec, list):
        return []
    if isinstance(spec, (str, int, float, bool)) or spec is None:
        return spec
    return None


def write_result(path, result):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# pipeline
# --------------------------------------------------------------------------
def run_pipeline(input_root, output_dir, work_dir, case_path, task_path,
                 skeleton_only=False):
    start_time = now_iso()
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    log_path = os.path.join(output_dir, "operation_log.jsonl")
    if not skeleton_only and os.path.exists(log_path):
        try:
            os.remove(log_path)
        except OSError:
            pass
    log_event(log_path, "skill", "start", "pipeline start (skeleton_only=%s)" % skeleton_only, start_time)

    case, case_err = load_case(case_path)
    task_text = None
    if os.path.isfile(task_path):
        task_text, _enc, _err = read_text_safe(task_path, 4 * 1024 * 1024)
        if task_text is None:
            task_text = None

    contract, contract_key = resolve_contract(case or {})

    # skeleton: case_id + contract placeholders, nothing else assumed
    computed_skel = {
        "contract_key": contract_key,
        "skeleton": True,
    }
    skeleton = build_result(
        case, contract, computed_skel, {},
        status="partial" if (case is not None and not case_err) else "input_error",
        limitations=[{"item": "output_contract", "detail": "contract missing"}] if not contract else [],
    )
    skeleton["change_log"].append({
        "time": now_iso(),
        "actor": "skill",
        "step": "skeleton",
        "detail": "wrote partial result skeleton; contract fields backfilled as null/[] placeholders",
    })
    if case_err:
        skeleton["errors"].append({"item": "case.json", "detail": case_err})
    write_result(os.path.join(output_dir, "result.json"), skeleton)

    if skeleton_only:
        log_event(log_path, "skill", "skeleton", "skeleton written; full analysis deferred", start_time)
        return skeleton, start_time

    # ---- full pipeline -------------------------------------------------
    entries, files, dirs, symlinks = build_inventory(input_root)
    contract_names = {"case.json", "task.md"}
    material_files = [f for f in files if f["path"] not in contract_names]
    dup_groups = build_duplicates(files)
    version_candidates = build_version_candidates(files)
    aliases = build_aliases(entries, symlinks)
    version_notes = extract_version_notes(input_root, files, task_text)
    catalogs = parse_catalogs(input_root, files)
    reconciliation = build_reconciliation(catalogs, files)
    index = build_index(entries)
    answers = answer_mechanical(
        entries, files, dup_groups, version_candidates, aliases, reconciliation
    )

    # ---- degradation / uncertainty / limitation ledger -----------------
    degradations, uncertainty, limitations, errors = [], [], [], []
    if case_err:
        errors.append({"item": "case.json", "detail": case_err})
    if not os.path.isdir(input_root):
        errors.append({"item": "/input", "detail": "input root missing or not a directory"})
    for e in entries:
        if e.get("error"):
            degradations.append({
                "path": e["path"],
                "type": e["type"],
                "detail": e["error"],
            })
        if e["type"] == "file" and e.get("hash_note"):
            degradations.append({
                "path": e["path"],
                "detail": "content hash skipped: %s" % e["hash_note"],
            })
        if e["type"] == "file" and e.get("read_note"):
            degradations.append({
                "path": e["path"],
                "detail": "preview degraded: %s" % e["read_note"],
            })
        if e["type"] == "file" and e.get("encoding", "").startswith("latin-1"):
            uncertainty.append({
                "item": e["path"],
                "severity": "medium",
                "reason": "text decoded with latin-1 fallback; encoding uncertain",
            })
    if version_candidates:
        uncertainty.append({
            "item": "version_candidates",
            "severity": "medium",
            "reason": "version chains are heuristic (normalized stem + hash + mtime); "
                      "manual confirmation required before treating as ground truth",
        })
    for a in aliases:
        if a.get("kind") == "symlink" and a.get("broken"):
            uncertainty.append({
                "item": a["path"],
                "severity": "high",
                "reason": "broken symlink: target %r does not resolve" % (a.get("target"),),
            })
    for c in catalogs:
        if c.get("error"):
            degradations.append({
                "path": c["path"],
                "detail": "catalog parse error: %s" % c["error"],
            })
    if reconciliation["missing"]:
        uncertainty.append({
            "item": "reconciliation.missing",
            "severity": "medium",
            "reason": "%d catalog-referenced files not found in inventory" % len(reconciliation["missing"]),
        })
    if not material_files:
        limitations.append({
            "item": "input",
            "detail": "no material files found (excluding case.json/task.md); library inventory is empty",
        })
    limitations.append({
        "item": "semantic_understanding",
        "detail": "engine provides deterministic mechanical layers only; "
                  "semantic field values (authors, years, project) are evidence "
                  "to be confirmed by the agent against source text, not inferred",
    })
    limitations.append({
        "item": "content_hash",
        "detail": "files larger than %d bytes have no content hash (recorded as too_large)" % DEFAULT_MAX_BYTES,
    })

    computed = {
        "contract_key": contract_key,
        "skeleton": False,
        "files": files,
        "directories": dirs,
        "symlinks": symlinks,
        "summary": {
            "total_entries": len(entries),
            "total_files": len(files),
            "material_files": len(material_files),
            "total_directories": len(dirs),
            "total_symlinks": len(symlinks),
            "duplicate_groups": len(dup_groups),
            "version_candidate_groups": len(version_candidates),
            "broken_symlinks": len([a for a in aliases if a.get("broken")]),
            "catalog_files": len(catalogs),
            "reconciliation": reconciliation["summary"],
        },
        "duplicate_groups": dup_groups,
        "version_candidates": version_candidates,
        "version_notes": version_notes,
        "aliases": aliases,
        "reconciliation": reconciliation,
        "answers": answers,
        "index": index,
        "inventory": entries,
    }

    # ---- serialize editable artifacts ----------------------------------
    artifacts = write_artifacts(
        output_dir, entries, files, dup_groups, version_candidates,
        version_notes, aliases, catalogs, reconciliation, index, answers
    )

    # ---- mechanical sources (locator + verbatim quote) ------------------
    sources = []
    for g in dup_groups[:20]:
        sources.append({
            "item": "duplicate_group",
            "locator": g["paths"][0],
            "quote": "sha256=%s; group=%s" % (g["sha256"], "; ".join(g["paths"])),
        })
    for v in version_candidates[:20]:
        sources.append({
            "item": "version_candidate",
            "locator": v["newest"],
            "quote": "stem=%r; chain=%s" % (v["stem"], " -> ".join(
                m["path"] for m in v["members"])),
        })
    for n in version_notes[:20]:
        sources.append({
            "item": "version_note",
            "locator": "%s#L%d" % (n["source"], n["line"]),
            "quote": n["text"],
        })

    status = "ok"
    if case_err or not os.path.isdir(input_root):
        status = "input_error"
    elif not material_files:
        status = "insufficient_evidence"
    elif degradations or uncertainty or errors:
        status = "partial"

    result = build_result(
        case, contract, computed, artifacts,
        sources=sources, uncertainty=uncertainty, limitations=limitations,
        status=status, errors=errors, degradations=degradations,
    )
    result["runtime"].update({
        "start_time": start_time,
        "end_time": now_iso(),
        "input_root": input_root,
        "output_dir": output_dir,
    })
    result["change_log"].append({
        "time": now_iso(),
        "actor": "skill",
        "step": "full_pipeline",
        "detail": "inventory=%d files=%d dup_groups=%d version_groups=%d broken_symlinks=%d"
                  % (len(entries), len(files), len(dup_groups),
                     len(version_candidates),
                     len([a for a in aliases if a.get("broken")])),
    })
    write_result(os.path.join(output_dir, "result.json"), result)
    log_event(log_path, "skill", "full_pipeline", "result.json and artifacts written", start_time)
    return result, start_time


def write_artifacts(output_dir, entries, files, dup_groups, version_candidates,
                    version_notes, aliases, catalogs, reconciliation, index, answers):
    manifest_csv = os.path.join(output_dir, "library_manifest.csv")
    tree_txt = os.path.join(output_dir, "directory_tree.txt")
    dedup_json = os.path.join(output_dir, "dedup_version.json")
    recovery_json = os.path.join(output_dir, "recovery_report.json")
    index_json = os.path.join(output_dir, "library_index.json")
    manifest_md = os.path.join(output_dir, "library_manifest.md")

    cols = ["path", "type", "size", "mtime", "mode", "sha256", "encoding",
            "link_target", "link_broken", "hash_note", "read_note", "preview"]
    with open(manifest_csv, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for e in entries:
            w.writerow([
                e.get("path", ""),
                e.get("type", ""),
                e.get("size", ""),
                e.get("mtime", ""),
                e.get("mode", ""),
                e.get("sha256", ""),
                e.get("encoding", ""),
                e.get("target", ""),
                e.get("link_broken", ""),
                e.get("hash_note", ""),
                e.get("read_note", ""),
                (e.get("preview") or "")[:200],
            ])

    with open(tree_txt, "w", encoding="utf-8") as fh:
        fh.write(build_tree(entries))

    dedup = {
        "duplicate_groups": dup_groups,
        "version_candidates": version_candidates,
        "version_candidates_note": "heuristic: normalized stem + content hash + mtime",
        "version_notes": version_notes,
        "aliases": aliases,
    }
    with open(dedup_json, "w", encoding="utf-8") as fh:
        json.dump(dedup, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    recovery = {
        "reconciliation": reconciliation,
        "catalog_files": [{"path": c["path"], "format": c["format"],
                           "status": c["status"], "error": c.get("error")} for c in catalogs],
    }
    with open(recovery_json, "w", encoding="utf-8") as fh:
        json.dump(recovery, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    with open(index_json, "w", encoding="utf-8") as fh:
        json.dump({"index": index, "answers": answers}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    md_lines = [
        "# 科研资料库交接清单",
        "",
        "> 由 skill 确定性引擎生成，可与源目录逐项核对。",
        "",
        "## 汇总",
        "",
    ]
    for k, v in answers.items():
        if k in ("broken_symlinks", "empty_directories", "reconciliation_summary"):
            continue
        md_lines.append("- %s: %s" % (k, v))
    md_lines += [
        "",
        "## 重复组（按内容 SHA-256）",
        "",
    ]
    for g in dup_groups:
        md_lines.append("- `%s` (%d 份): %s" % (g["sha256"][:12], g["count"], "; ".join(g["paths"])))
    md_lines += [
        "",
        "## 版本候选链（启发式，需人工确认）",
        "",
    ]
    for v in version_candidates:
        md_lines.append("- 主干 `%s`: %s" % (v["stem"], " -> ".join(m["path"] for m in v["members"])))
    with open(manifest_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md_lines) + "\n")

    return {
        "library_manifest.csv": manifest_csv,
        "directory_tree.txt": tree_txt,
        "dedup_version.json": dedup_json,
        "recovery_report.json": recovery_json,
        "library_index.json": index_json,
        "library_manifest.md": manifest_md,
    }


# --------------------------------------------------------------------------
# update / check / selftest
# --------------------------------------------------------------------------
def deep_merge(base, patch):
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return patch
    out = OrderedDict(base)
    for k, v in patch.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def cmd_update(patch_path, result_path):
    if not os.path.isfile(result_path):
        print("update: result.json not found: %s" % result_path, file=sys.stderr)
        return 2
    with open(result_path, "r", encoding="utf-8") as fh:
        base = json.load(fh)
    with open(patch_path, "r", encoding="utf-8-sig") as fh:
        patch = json.load(fh)
    merged = deep_merge(base, patch)
    merged.setdefault("change_log", []).append({
        "time": now_iso(),
        "actor": "agent",
        "step": "update",
        "detail": "merged patch %s" % patch_path,
    })
    write_result(result_path, merged)
    print("update: merged %s into %s" % (patch_path, result_path))
    return 0


def cmd_check(result_path, case_path=None):
    problems = []
    if not os.path.isfile(result_path):
        print("check: FAIL — result.json missing", file=sys.stderr)
        return 1
    try:
        with open(result_path, "r", encoding="utf-8") as fh:
            r = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print("check: FAIL — result.json invalid JSON: %s" % exc, file=sys.stderr)
        return 1
    if not r.get("case_id"):
        problems.append("case_id empty")
    if r.get("status") not in STATUSES:
        problems.append("status not in %s" % sorted(STATUSES))
    if "sources" not in r:
        problems.append("sources missing")
    if "uncertainty" not in r:
        problems.append("uncertainty missing")
    if "change_log" not in r:
        problems.append("change_log missing")
    if "limitations" not in r:
        problems.append("limitations missing")
    if case_path and os.path.isfile(case_path):
        case, _err = load_case(case_path)
        contract, _key = resolve_contract(case or {})
        missing = [f for f in contract if f not in r and ("_contract_" + f) not in r.get("computed", {})]
        if missing:
            problems.append("contract fields missing from result: %s" % ", ".join(missing))
    if problems:
        print("check: FAIL — " + "; ".join(problems), file=sys.stderr)
        return 1
    print("check: ok — case_id=%r status=%r" % (r.get("case_id"), r.get("status")))
    return 0


def selftest():
    """Create a synthetic library in a temp dir and run the full pipeline."""
    tmp = tempfile.mkdtemp(prefix="library_selftest_")
    root = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    os.makedirs(os.path.join(root, "sub", "empty"), exist_ok=True)
    os.makedirs(out, exist_ok=True)

    def w(rel, content):
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(content)

    w("a.txt", b"hello world\n")
    w("sub/b.txt", b"hello world\n")          # duplicate of a.txt
    w("paper_v1.md", "# draft\none\n".encode("utf-8"))
    w("paper_v2.md", "# draft\none two\n".encode("utf-8"))
    w("paper_final.md", "# draft\none two three\n".encode("utf-8"))
    w("catalog.json", json.dumps({"files": [
        {"path": "a.txt"}, {"path": "missing.txt"}
    ]}).encode("utf-8"))
    w("bad.json", b"{not valid json")
    w("badenc.txt", b"\xff\xfe\x00bad")
    symlinks_created = False
    try:
        os.symlink("a.txt", os.path.join(root, "alias.txt"))
        symlinks_created = True
    except (OSError, NotImplementedError):
        pass
    try:
        os.symlink("does_not_exist.txt", os.path.join(root, "broken.txt"))
        symlinks_created = True
    except (OSError, NotImplementedError):
        pass

    case_path = os.path.join(root, "case.json")
    with open(case_path, "w", encoding="utf-8") as fh:
        json.dump({
            "case_id": "selftest-case",
            "output_contract": {
                "inventory": {"type": "array"},
                "reconciliation_summary": {"type": "object"},
                "notes": {"type": "string"},
            },
        }, fh, ensure_ascii=False)
    task_path = os.path.join(root, "task.md")
    with open(task_path, "w", encoding="utf-8") as fh:
        fh.write("# task\n\n版本说明：paper_v2 替代 paper_v1。\n")

    result, _ = run_pipeline(root, out, work, case_path, task_path, skeleton_only=False)
    rpath = os.path.join(out, "result.json")
    ok = True
    if not os.path.isfile(rpath):
        print("selftest: FAIL result.json missing")
        ok = False
    else:
        with open(rpath, "r", encoding="utf-8") as fh:
            r = json.load(fh)
        checks = {
            "case_id": r.get("case_id") == "selftest-case",
            "status_allowed": r.get("status") in STATUSES,
            "duplicate_group_found": len(r["computed"]["duplicate_groups"]) >= 1,
            "version_chain_found": len(r["computed"]["version_candidates"]) >= 1,
            "bad_json_recorded": any("bad.json" in (d.get("path") or "") for d in r.get("degradations", [])),
            "contract_filled": "inventory" in r and "reconciliation_summary" in r and "notes" in r,
        }
        if symlinks_created:
            checks["broken_symlink_found"] = any(
                a.get("broken") for a in r["computed"]["aliases"]
            )
        for name, passed in checks.items():
            if not passed:
                print("selftest: FAIL %s" % name)
                ok = False
        for art in ("library_manifest.csv", "directory_tree.txt",
                    "dedup_version.json", "recovery_report.json",
                    "library_index.json", "library_manifest.md"):
            if not os.path.isfile(os.path.join(out, art)):
                print("selftest: FAIL missing artifact %s" % art)
                ok = False
        # check subcommand
        if cmd_check(rpath, case_path) != 0:
            ok = False
    print("selftest: %s (tmp=%s)" % ("ok" if ok else "FAIL", tmp))
    return 0 if ok else 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="deterministic document-library repair engine (stdlib only)",
    )
    p.add_argument("--case-json", default="/input/case.json")
    p.add_argument("--task-path", default="/input/task.md")
    p.add_argument("--input-root", default="/input")
    p.add_argument("--output-dir", default="/app/output")
    p.add_argument("--work-dir", default="/app/work")
    p.add_argument("--skeleton-only", action="store_true",
                   help="write partial result.json skeleton only, then exit 0")
    p.add_argument("--update", metavar="PATCH.json",
                   help="deep-merge a JSON patch into an existing result.json")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--check", metavar="RESULT.json",
                   help="validate result.json; use with --case-json")
    p.add_argument("--selftest", action="store_true",
                   help="run built-in synthetic-library self-test and exit")
    p.add_argument("--version", action="version", version="core_impl %s" % VERSION)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.selftest:
        return selftest()
    if args.update:
        return cmd_update(args.update, args.result)
    if args.check:
        return cmd_check(args.check, args.case_json)
    try:
        run_pipeline(
            args.input_root, args.output_dir, args.work_dir,
            args.case_json, args.task_path,
            skeleton_only=args.skeleton_only,
        )
    except OSError as exc:
        # Skeleton result.json (when already written) is preserved; report
        # the write failure cleanly instead of crashing with a traceback.
        print("write_failure: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — never leave the harness hanging
        print("unexpected_error: %s: %s" % (exc.__class__.__name__, exc), file=sys.stderr)
        return 1
    print("finalize: wrote %s" % os.path.join(args.output_dir, "result.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
