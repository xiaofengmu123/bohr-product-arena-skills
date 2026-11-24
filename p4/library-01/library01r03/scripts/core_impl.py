"""core_impl.py — deterministic document-library repair pipeline (Python stdlib only).

Design goals
------------
1. Determinism: for the same input tree the computed inventory, hashes,
   duplicate groups, version candidates, aliases, reconciliation, index and
   answers are byte-identical across runs. Only wall-clock timestamps in
   `runtime` and `change_log[*].time` may differ; selftest.py normalizes them.
2. Honest status: `ok | partial | insufficient_evidence | input_error`.
3. Read-only inputs: the source tree is never moved, overwritten or deleted.
4. Tolerance: empty dirs, bad JSON, bad encodings, unreadable files, symlinks,
   hard links, oversized files and missing SQLite databases are all recorded
   and never abort the run.

Command surface
---------------
    core_impl.py --input DIR --output DIR --work DIR --case-json F --task-path F
    core_impl.py --skeleton-only   # write partial skeleton result first
    core_impl.py --update PATCH.json --result R   # incremental merge
    core_impl.py --check R --case-json F          # contract self-check
"""
import argparse
import csv
import difflib
import hashlib
import io
import json
import os
import re
import stat
import sys
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "errors", "degradations", "runtime", "limitations", "change_log",
    "computed", "artifacts",
}
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CONTRACT_KEYS = ("output_contract", "output", "result_schema", "contract")

MAX_TEXT_BYTES = 2 * 1024 * 1024        # text parsing / diff limit
MAX_HASH_BYTES = 256 * 1024 * 1024      # content-hash limit
HASH_CHUNK = 1024 * 1024

TEXT_EXTENSIONS = {
    ".md", ".txt", ".rst", ".csv", ".tsv", ".json", ".bib", ".ris",
    ".yaml", ".yml", ".xml", ".html", ".htm", ".ini", ".cfg", ".log",
    ".tex", ".py", ".r", ".ipynb", ".sql",
}
CATALOG_JSON = {".json"}
CATALOG_CSV = {".csv", ".tsv"}
CATALOG_BIB = {".bib", ".bibtex"}
CATALOG_RIS = {".ris"}
CATALOG_SQLITE = {".sqlite", ".sqlite3", ".db"}


# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------
def stable_key(value: str) -> tuple:
    """Case-folded primary key plus the raw value breaks ties deterministically."""
    return (value.casefold(), value)


def to_posix(path: str) -> str:
    return path.replace(os.sep, "/")


def rel_posix(root: str, path: str) -> str:
    return to_posix(os.path.relpath(path, root))


def utc_iso(ts: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def read_bytes(path: str, errors: list, max_bytes: int = MAX_HASH_BYTES):
    """Read up to max_bytes; record a degradation if truncated or unreadable."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(max_bytes + 1)
        if len(data) > max_bytes:
            errors.append({
                "path": path, "kind": "too_large",
                "detail": "content larger than %d bytes, hash truncated" % max_bytes,
            })
            return data[:max_bytes]
        return data
    except OSError as exc:
        errors.append({"path": path, "kind": "unreadable", "detail": str(exc)})
        return None


def sha256_of(path: str, errors: list):
    """SHA-256 of a regular file; None on failure or oversize."""
    h = hashlib.sha256()
    total = 0
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(HASH_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_HASH_BYTES:
                    errors.append({
                        "path": path, "kind": "too_large",
                        "detail": "larger than %d bytes, content not hashed"
                                  % MAX_HASH_BYTES,
                    })
                    return None
                h.update(chunk)
        return h.hexdigest()
    except OSError as exc:
        errors.append({"path": path, "kind": "unreadable", "detail": str(exc)})
        return None


def read_text(path: str, errors: list, max_bytes: int = MAX_TEXT_BYTES):
    """Read text with encoding fallback; None for binary/oversized/unreadable."""
    raw = read_bytes(path, errors, max_bytes)
    if raw is None:
        return None
    if b"\x00" in raw[:8192]:
        return None  # binary
    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    errors.append({
        "path": path, "kind": "bad_encoding",
        "detail": "could not decode as utf-8/cp1252",
    })
    return raw.decode("utf-8", errors="replace")


def write_json(path: str, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def read_json(path: str, errors: list):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except FileNotFoundError:
        errors.append({"path": path, "kind": "missing", "detail": "file not found"})
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        errors.append({"path": path, "kind": "bad_json", "detail": str(exc)})
        return None
    except OSError as exc:
        errors.append({"path": path, "kind": "unreadable", "detail": str(exc)})
        return None


def deep_merge(base: dict, patch: dict) -> dict:
    """Recursively merge patch into base (dicts merge, others replace)."""
    for key, value in patch.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


# ---------------------------------------------------------------------------
# Input discovery (read-only, deterministic)
# ---------------------------------------------------------------------------
def inventory_tree(root: str, skip_names: set):
    """Return (entries, errors). Every entry has rel_path and type; files get
    size/mtime/mode/inode/nlink/sha256/encoding/link_target as applicable."""
    entries = []
    errors = []

    def visit(path: str, rel: str):
        try:
            with os.scandir(path) as it:
                children = sorted(it, key=lambda e: stable_key(e.name))
        except OSError as exc:
            errors.append({"path": rel or ".", "kind": "scandir_error",
                           "detail": str(exc)})
            return
        for child in children:
            name = child.name
            child_rel = (rel + "/" + name) if rel else name
            if name in skip_names:
                continue
            try:
                st = child.stat(follow_symlinks=False)
            except OSError as exc:
                errors.append({"path": child_rel, "kind": "stat_error",
                               "detail": str(exc)})
                entries.append({
                    "rel_path": child_rel, "type": "unknown",
                    "mode_octal": None, "size": None, "mtime": None,
                    "mtime_unix": None, "nlink": None, "inode": None,
                    "st_dev": None, "sha256": None, "encoding": None,
                    "link_target": None, "content_note": "stat_error",
                })
                continue

            mode = stat.S_IMODE(st.st_mode)
            base = {
                "rel_path": child_rel,
                "type": None,
                "mode_octal": "0o%o" % mode,
                "size": st.st_size,
                "mtime": utc_iso(st.st_mtime),
                "mtime_unix": st.st_mtime,
                "nlink": st.st_nlink,
                "inode": st.st_ino,
                "st_dev": st.st_dev,
            }

            if stat.S_ISLNK(st.st_mode):
                base["type"] = "symlink"
                base["size"] = None
                try:
                    base["link_target"] = os.readlink(path and os.path.join(path, name) or name)
                except OSError as exc:
                    base["link_target"] = None
                    errors.append({"path": child_rel, "kind": "readlink_error",
                                   "detail": str(exc)})
                try:
                    child.stat(follow_symlinks=True)
                    base["content_note"] = "symlink_ok"
                except OSError:
                    base["content_note"] = "broken_symlink"
                    base["type"] = "broken_symlink"
                    errors.append({"path": child_rel, "kind": "broken_symlink",
                                   "detail": "target missing or unreadable"})
                entries.append(base)
                continue

            if stat.S_ISDIR(st.st_mode):
                base["type"] = "dir"
                base["size"] = None
                base["sha256"] = None
                base["encoding"] = None
                base["link_target"] = None
                base["content_note"] = None
                entries.append(base)
                full = os.path.join(path, name) if path else name
                visit(full, child_rel)
                continue

            if stat.S_ISREG(st.st_mode):
                base["type"] = "file"
                full = os.path.join(path, name) if path else name
                base["link_target"] = None
                base["sha256"] = sha256_of(full, errors)
                base["encoding"] = None
                base["content_note"] = None
                if base["sha256"] is None:
                    base["content_note"] = "unhashed"
                else:
                    ext = os.path.splitext(name)[1].lower()
                    if ext in TEXT_EXTENSIONS or base["size"] <= MAX_TEXT_BYTES:
                        txt = read_text(full, errors)
                        if txt is None:
                            base["encoding"] = "binary" if ext not in TEXT_EXTENSIONS else "undecodable"
                        else:
                            base["encoding"] = "text"
                entries.append(base)
                continue

            base["type"] = "other"
            base["sha256"] = None
            base["encoding"] = None
            base["link_target"] = None
            base["content_note"] = "non_regular"
            entries.append(base)

    visit(root, "")
    entries.sort(key=lambda e: stable_key(e["rel_path"]))
    return entries, errors


# ---------------------------------------------------------------------------
# Deduplication (content SHA-256, never filename)
# ---------------------------------------------------------------------------
def group_duplicates(files):
    by_hash = {}
    for f in files:
        if f.get("type") == "file" and f.get("sha256"):
            key = (f["sha256"], f["size"])
            by_hash.setdefault(key, []).append(f["rel_path"])
    groups = [sorted(g, key=stable_key) for g in by_hash.values() if len(g) > 1]
    groups.sort(key=lambda g: stable_key(g[0]))
    return [{"sha256": f, "size": s, "members": m}
            for (f, s), m in sorted(by_hash.items(), key=lambda kv: stable_key(kv[1][0]))
            if len(m) > 1]


# ---------------------------------------------------------------------------
# Version candidates (normalized stem + content/mtime, heuristic)
# ---------------------------------------------------------------------------
_VERSION_TOKEN = re.compile(
    r"\b(v|ver|version|rev|r)?\s?\d+(?:\.\d+)*(?:[-_.]?[a-z0-9]+)?\b", re.I
)
_VERSION_WORD = re.compile(
    r"\b(final|draft|copy|backup|old|new|latest|clean|edited|revised)\b", re.I
)


def normalize_stem(name: str) -> str:
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = stem.casefold()
    stem = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", stem)
    stem = _VERSION_TOKEN.sub(" ", stem)
    stem = _VERSION_WORD.sub(" ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem


def diff_summary(a_path: str, b_path: str, errors: list):
    """Line-level diff summary for text files <= MAX_TEXT_BYTES."""
    a = read_text(a_path, errors)
    b = read_text(b_path, errors)
    if a is None or b is None:
        return None
    a_lines = a.splitlines()
    b_lines = b.splitlines()
    sm = difflib.SequenceMatcher(a=a_lines, b=b_lines, autojunk=False)
    added = removed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            added += j2 - j1
        elif tag == "delete":
            removed += i2 - i1
        elif tag == "replace":
            added += max(0, j2 - j1)
            removed += max(0, i2 - i1)
    return {"lines_added": added, "lines_removed": removed,
            "similarity_ratio": round(sm.ratio(), 4)}


def group_versions(files, input_root):
    stems = {}
    for f in files:
        if f.get("type") != "file":
            continue
        stems.setdefault(normalize_stem(f["rel_path"]), []).append(f)
    groups = []
    for stem, members in stems.items():
        if len(members) < 2 or not stem:
            continue
        members = sorted(members, key=lambda f: (f["mtime_unix"] or 0, stable_key(f["rel_path"])))
        chain = []
        for i, cur in enumerate(members):
            nxt = members[i + 1] if i + 1 < len(members) else None
            item = {
                "path": cur["rel_path"],
                "sha256": cur["sha256"],
                "mtime": cur["mtime"],
                "superseded_by": nxt["rel_path"] if nxt else None,
                "supersedes": members[i - 1]["rel_path"] if i > 0 else None,
                "diff": None,
            }
            if nxt:
                item["diff"] = diff_summary(
                    os.path.join(input_root, cur["rel_path"]),
                    os.path.join(input_root, nxt["rel_path"]),
                    [],
                )
            chain.append(item)
        groups.append({"normalized_stem": stem, "heuristic": True, "members": chain})
    groups.sort(key=lambda g: stable_key(g["normalized_stem"]))
    return groups


# ---------------------------------------------------------------------------
# Aliases: hard links + symlinks
# ---------------------------------------------------------------------------
def group_aliases(files):
    hard_groups = {}
    symlinks = []
    for f in files:
        if f.get("type") == "file" and f.get("nlink", 1) > 1 and f.get("inode") is not None:
            key = (f.get("st_dev"), f.get("inode"))
            hard_groups.setdefault(key, []).append(f["rel_path"])
        if f.get("type") in ("symlink", "broken_symlink"):
            symlinks.append({
                "path": f["rel_path"],
                "target": f.get("link_target"),
                "broken": f.get("type") == "broken_symlink",
            })
    hard = [sorted(g, key=stable_key) for g in hard_groups.values() if len(g) > 1]
    hard.sort(key=lambda g: stable_key(g[0]))
    symlinks.sort(key=lambda s: stable_key(s["path"]))
    return hard, symlinks


# ---------------------------------------------------------------------------
# Administrative version-note parsing
# ---------------------------------------------------------------------------
_PAT_DATE = re.compile(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b")
_PAT_VERSION = re.compile(
    r"(?i)\bversion\s*[:=]?\s*([vV]?\d+(?:\.\d+)+(?:[-_.]?[a-zA-Z0-9]+)?)"
)
_PAT_SUPERSEDE = re.compile(
    r"(?i)\b(supersedes?|replaces?|deprecat(?:ed|es)?|obsolete(?:s|d)?|replaced\s+by)\b"
)


def parse_version_notes(files, input_root: str):
    notes = []
    for f in files:
        if f.get("type") != "file":
            continue
        ext = os.path.splitext(f["rel_path"])[1].lower()
        if ext not in (".md", ".txt", ".rst", ".yaml", ".yml", ".json"):
            continue
        path = os.path.join(input_root, f["rel_path"])
        text = read_text(path, [])
        if not text:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            kind = None
            value = None
            m = _PAT_VERSION.search(stripped)
            if m:
                kind, value = "version", m.group(1)
            elif _PAT_SUPERSEDE.search(stripped):
                kind, value = "supersede", stripped
            elif _PAT_DATE.search(stripped) and any(
                k in stripped.casefold() for k in ("version", "v1", "v2", "date", "改", "版")
            ):
                kind, value = "date", stripped
            if kind:
                notes.append({
                    "source": f["rel_path"],
                    "line": lineno,
                    "kind": kind,
                    "value": value,
                    "quote": stripped[:400],
                })
    notes.sort(key=lambda n: stable_key(n["source"] + "#" + str(n["line"])))
    return notes


# ---------------------------------------------------------------------------
# Catalog parsing: JSON / CSV / BibTeX / RIS / SQLite
# ---------------------------------------------------------------------------
def _records_from_dict(obj, source):
    records = []
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, dict):
                records.append(dict(item, **{"_source": source, "_index": i}))
            else:
                records.append({"_source": source, "_index": i, "value": item})
    elif isinstance(obj, dict):
        if any(isinstance(v, (list, dict)) for v in obj.values()):
            for i, (k, v) in enumerate(obj.items()):
                if isinstance(v, dict):
                    records.append(dict(v, **{"_source": source, "_index": i, "_key": k}))
                elif isinstance(v, list):
                    for j, item in enumerate(v):
                        if isinstance(item, dict):
                            records.append(dict(item, **{"_source": source, "_index": i, "_sub": j}))
                        else:
                            records.append({"_source": source, "_index": i, "_sub": j, "value": item})
                else:
                    records.append({"_source": source, "_key": k, "value": v})
        else:
            records.append(dict(obj, **{"_source": source, "_index": 0}))
    return records


def _parse_bibtex(text, source):
    records = []
    pattern = re.compile(
        r"@\s*([a-zA-Z]+)\s*[{(]\s*([^,}]*)\s*,\s*(.*?)\s*[)}]\s*(?:,|\Z)",
        re.S,
    )
    for m in pattern.finditer(text):
        etype, key, body = m.group(1), m.group(2), m.group(3)
        rec = {"_source": source, "type": etype, "key": key.strip()}
        field = re.compile(
            r"([a-zA-Z][a-zA-Z0-9_-]*)\s*=\s*[{\"](.*?)[}\"]", re.S
        )
        for fm in field.finditer(body):
            rec[fm.group(1).lower()] = fm.group(2).strip()
        records.append(rec)
    return records


def _parse_ris(text, source):
    records = []
    current = None
    for line in text.splitlines():
        if len(line) >= 2 and line[2:4] in ("  ", " -"):
            tag, value = line[0:2], line[6:].strip() if len(line) > 6 else ""
            if tag == "TY":
                current = {"_source": source, "type": value}
                records.append(current)
            elif current is not None:
                key = {"TI": "title", "AU": "author", "PY": "year",
                       "DO": "doi", "JO": "journal", "ID": "id",
                       "UR": "url", "ER": None}.get(tag, tag.lower())
                if key:
                    if key in current:
                        if not isinstance(current[key], list):
                            current[key] = [current[key]]
                        current[key].append(value)
                    else:
                        current[key] = value
    return records


def _parse_sqlite(path, errors):
    import sqlite3
    out = {"tables": []}
    try:
        con = sqlite3.connect(path)
    except sqlite3.Error as exc:
        errors.append({"path": path, "kind": "sqlite_open_error", "detail": str(exc)})
        return None
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        for (tname,) in cur.fetchall():
            table = {"name": tname, "columns": [], "rows": []}
            try:
                cur.execute('SELECT * FROM "%s" LIMIT 200' % tname.replace('"', '""'))
                table["columns"] = [d[0] for d in cur.description or []]
                for row in cur.fetchall():
                    table["rows"].append(list(row))
            except sqlite3.Error as exc:
                table["error"] = str(exc)
                errors.append({"path": path, "kind": "sqlite_query_error",
                               "detail": "%s: %s" % (tname, exc)})
            out["tables"].append(table)
    finally:
        con.close()
    return out


def parse_catalogs(files, input_root, errors):
    catalogs = []
    records = []
    for f in files:
        if f.get("type") != "file":
            continue
        ext = os.path.splitext(f["rel_path"])[1].lower()
        path = os.path.join(input_root, f["rel_path"])
        if ext in CATALOG_JSON:
            obj = read_json(path, errors)
            if obj is None:
                catalogs.append({"path": f["rel_path"], "format": "json", "records": 0,
                                 "error": "parse_failed"})
                continue
            recs = _records_from_dict(obj, f["rel_path"])
            catalogs.append({"path": f["rel_path"], "format": "json", "records": len(recs)})
            records.extend(recs)
        elif ext in CATALOG_CSV:
            text = read_text(path, errors)
            if text is None:
                catalogs.append({"path": f["rel_path"], "format": "csv", "records": 0,
                                 "error": "parse_failed"})
                continue
            try:
                rows = list(csv.DictReader(io.StringIO(text)))
                for i, row in enumerate(rows):
                    records.append(dict(row, **{"_source": f["rel_path"], "_index": i}))
                catalogs.append({"path": f["rel_path"], "format": "csv", "records": len(rows)})
            except csv.Error as exc:
                errors.append({"path": path, "kind": "csv_parse_error", "detail": str(exc)})
                catalogs.append({"path": f["rel_path"], "format": "csv", "records": 0,
                                 "error": "parse_failed"})
        elif ext in CATALOG_BIB:
            text = read_text(path, errors)
            if text is not None:
                recs = _parse_bibtex(text, f["rel_path"])
                records.extend(recs)
                catalogs.append({"path": f["rel_path"], "format": "bibtex", "records": len(recs)})
        elif ext in CATALOG_RIS:
            text = read_text(path, errors)
            if text is not None:
                recs = _parse_ris(text, f["rel_path"])
                records.extend(recs)
                catalogs.append({"path": f["rel_path"], "format": "ris", "records": len(recs)})
        elif ext in CATALOG_SQLITE:
            parsed = _parse_sqlite(path, errors)
            if parsed is not None:
                n = sum(len(t.get("rows", [])) for t in parsed["tables"])
                catalogs.append({"path": f["rel_path"], "format": "sqlite",
                                 "tables": len(parsed["tables"]), "records": n})
                for ti, t in enumerate(parsed["tables"]):
                    for ri, row in enumerate(t.get("rows", [])):
                        rec = dict(zip(t["columns"], row)) if t["columns"] else {"value": row}
                        rec.update({"_source": f["rel_path"], "_table": t["name"], "_index": ri})
                        records.append(rec)
    catalogs.sort(key=lambda c: stable_key(c["path"]))
    return catalogs, records


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------
_FILENAME_KEYS = ("file", "filename", "path", "name", "title", "attachment",
                  "file_path", "source", "fulltext", "pdf")


def reconcile(records, files):
    file_basenames = {}
    for f in files:
        if f.get("type") == "file":
            b = os.path.basename(f["rel_path"])
            file_basenames.setdefault(b.casefold(), []).append(f["rel_path"])

    matches = []
    missing = []
    mismatches = []
    referenced = set()

    for rec in records:
        cand = None
        for key in _FILENAME_KEYS:
            if key in rec and isinstance(rec[key], str) and rec[key].strip():
                cand = rec[key].strip()
                break
        if not cand:
            cand = rec.get("_key")
        if not cand or not isinstance(cand, str):
            missing.append(rec)
            continue
        base = os.path.basename(cand.replace("\\", "/"))
        fold = base.casefold()
        if fold in file_basenames:
            for path in file_basenames[fold]:
                referenced.add(path)
                matches.append({"record": rec, "file": path, "matched_by": "filename"})
        elif "/" in cand.replace("\\", "/") or "\\" in cand:
            mismatches.append({"record": rec, "candidate": cand, "reason": "path not found"})
        else:
            missing.append(rec)

    orphaned = sorted(
        [f["rel_path"] for f in files if f.get("type") == "file"
         and f["rel_path"] not in referenced],
        key=stable_key,
    )
    matches.sort(key=lambda m: stable_key(m["file"] + m["record"].get("_source", "")))
    missing.sort(key=lambda m: stable_key(
        m.get("_source", "") + str(m.get("_index", 0))))
    mismatches.sort(key=lambda m: stable_key(m.get("candidate", "")))
    return {
        "matches": len(matches),
        "mismatches": mismatches,
        "missing": missing,
        "orphaned": orphaned,
        "detail_matches": matches,
    }


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------
def build_index(files):
    by_ext = {}
    by_name = {}
    for f in files:
        if f.get("type") != "file":
            continue
        ext = os.path.splitext(f["rel_path"])[1].lower() or "(none)"
        by_ext.setdefault(ext, []).append(f["rel_path"])
        by_name.setdefault(os.path.basename(f["rel_path"]).casefold(), []).append(f["rel_path"])
    for d in (by_ext, by_name):
        for k in d:
            d[k].sort(key=stable_key)
    return {
        "by_extension": {k: v for k, v in sorted(by_ext.items(), key=lambda kv: stable_key(kv[0]))},
        "by_name": {k: v for k, v in sorted(by_name.items(), key=lambda kv: stable_key(kv[0]))},
    }


def compute_answers(files, dirs, dup_groups, hard_groups, symlinks, version_groups,
                    version_notes, reconciliation):
    ext_counts = {}
    for f in files:
        if f.get("type") == "file":
            ext = os.path.splitext(f["rel_path"])[1].lower() or "(none)"
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
    return {
        "total_entries": len(files),
        "total_files": sum(1 for f in files if f.get("type") == "file"),
        "total_dirs": sum(1 for f in files if f.get("type") == "dir"),
        "total_symlinks": sum(1 for f in files if f.get("type") in ("symlink", "broken_symlink")),
        "total_bytes": sum(f.get("size") or 0 for f in files if f.get("type") == "file"),
        "extensions": {k: v for k, v in sorted(ext_counts.items(), key=lambda kv: stable_key(kv[0]))},
        "duplicate_groups": len(dup_groups),
        "duplicate_members": sum(len(g["members"]) for g in dup_groups),
        "hardlink_groups": len(hard_groups),
        "broken_symlinks": [s["path"] for s in symlinks if s.get("broken")],
        "version_candidate_groups": len(version_groups),
        "version_notes": len(version_notes),
        "orphaned_files": reconciliation["orphaned"],
        "missing_records": len(reconciliation["missing"]),
        "record_mismatches": len(reconciliation["mismatches"]),
    }


# ---------------------------------------------------------------------------
# Contract handling
# ---------------------------------------------------------------------------
def extract_contract(case_data, errors):
    if not isinstance(case_data, dict):
        return None
    for key in CONTRACT_KEYS:
        if key in case_data:
            return case_data[key]
    if "result_schema" in case_data:
        return case_data["result_schema"]
    return None


def contract_fields(contract):
    """Return an ordered list of field names described by the contract."""
    if contract is None:
        return []
    if isinstance(contract, list):
        fields = []
        for item in contract:
            if isinstance(item, str):
                fields.append(item)
            elif isinstance(item, dict):
                fields.extend(k for k in item if not k.startswith("$"))
        return fields
    if isinstance(contract, dict):
        if "properties" in contract and isinstance(contract["properties"], dict):
            return list(contract["properties"].keys())
        if "fields" in contract and isinstance(contract["fields"], (list, dict)):
            if isinstance(contract["fields"], dict):
                return list(contract["fields"].keys())
            return contract_fields(contract["fields"])
        return [k for k in contract if not k.startswith("$")]
    return []


def default_contract_value(field, contract):
    if isinstance(contract, dict):
        if "properties" in contract and isinstance(contract["properties"], dict):
            prop = contract["properties"].get(field, {})
            t = prop.get("type")
            if t == "array":
                return []
            if t in ("object",):
                return {}
        if field in contract and isinstance(contract[field], dict):
            t = contract[field].get("type")
            if t == "array":
                return []
            if t == "object":
                return {}
    return None


def merge_contract(result, contract):
    """Back-fill all contract fields; reserved-key collisions move to computed."""
    if contract is None:
        result.setdefault("computed", {})["_contract"] = None
        result.setdefault("uncertainty", []).append(
            {"item": "output_contract", "severity": "medium",
             "reason": "case.json did not expose an output_contract field"})
        return
    result.setdefault("computed", {})["_contract"] = contract
    for field in contract_fields(contract):
        value = default_contract_value(field, contract)
        if field in RESERVED_KEYS:
            result.setdefault("computed", {})["_contract_" + field] = value
        else:
            result.setdefault(field, value)


def detect_case_id(case_data, task_path):
    if isinstance(case_data, dict):
        for key in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
            v = case_data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (int, float)):
                return str(v)
    if task_path and os.path.isfile(task_path):
        text = read_text(task_path, []) or ""
        m = re.search(r"challenge[-_]?[A-Za-z0-9]+", text)
        if m:
            return m.group(0)
        m = re.search(r"case[-_]?[A-Za-z0-9]+", text, re.I)
        if m:
            return m.group(0)
    return ""


# ---------------------------------------------------------------------------
# Artifact writers
# ---------------------------------------------------------------------------
def write_manifest_csv(files, path):
    cols = ["rel_path", "type", "size", "mtime", "sha256", "encoding",
            "mode_octal", "nlink", "inode", "link_target", "content_note"]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for f in files:
            w.writerow([f.get(c, "") for c in cols])


def write_directory_tree(files, path):
    tree = {}
    for f in files:
        parts = f["rel_path"].split("/")
        node = tree
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                node.setdefault(part, {"entry": f})
            else:
                node = node.setdefault(part, {})

    lines = []

    def emit(node, prefix, name):
        entry = node.get("entry") if isinstance(node, dict) else None
        if entry is None:
            # directory (or intermediate)
            lines.append(prefix + name + "/")
            children = [(k, v) for k, v in node.items() if k != "entry"]
            children.sort(key=lambda kv: stable_key(kv[0]))
            if not children:
                lines.append(prefix + "    [空目录]")
            for i, (k, v) in enumerate(children):
                last = i == len(children) - 1
                emit(v, prefix + ("    " if last else "│   "), k + ("" if isinstance(v, dict) and "entry" not in v else ""))
        else:
            suffix = ""
            if entry.get("type") == "broken_symlink":
                suffix = " -> %s [断链]" % (entry.get("link_target") or "?")
            elif entry.get("type") == "symlink":
                suffix = " -> %s" % (entry.get("link_target") or "?")
            lines.append(prefix + name + suffix)

    # emit from root
    roots = [(k, v) for k, v in tree.items()]
    roots.sort(key=lambda kv: stable_key(kv[0]))
    for i, (k, v) in enumerate(roots):
        emit(v, "", k)

    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def write_library_manifest_md(result, files, path):
    a = result.get("computed", {}).get("answers", {})
    lines = [
        "# 科研资料库交接报告",
        "",
        "- case_id: `%s`" % result.get("case_id", ""),
        "- status: `%s`" % result.get("status", ""),
        "",
        "## 概要",
        "",
        "- 条目总数: %s" % a.get("total_entries", 0),
        "- 文件数: %s" % a.get("total_files", 0),
        "- 目录数: %s" % a.get("total_dirs", 0),
        "- 重复组: %s" % a.get("duplicate_groups", 0),
        "- 版本候选组: %s" % a.get("version_candidate_groups", 0),
        "- 硬链接组: %s" % a.get("hardlink_groups", 0),
        "- 断链符号链接: %s" % len(a.get("broken_symlinks", [])),
        "",
        "## 产物",
        "",
        "| 文件 | 说明 |",
        "|-|-|",
        "| library_manifest.csv | 逐文件清单（路径/大小/哈希/权限/来源） |",
        "| directory_tree.txt | 目录树，空目录标注 |",
        "| dedup_version.json | 重复组、版本候选、版本说明、别名 |",
        "| recovery_report.json | 恢复对账（matches/mismatches/missing/orphaned） |",
        "| library_index.json | 按扩展名/文件名检索索引 |",
        "| operation_log.jsonl | 操作时间线 |",
        "",
        "本报告由确定性流水线从输入目录生成；未编造任何文件内容、哈希或元数据。",
        "",
    ]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------
def decide_status(case_data, case_id, files, errors):
    fatal = [e for e in errors if e.get("kind") in ("bad_json", "missing")]
    if not case_id or (case_data is None and case_id == ""):
        # No identifiable case -> cannot answer the contract
        if not any(f.get("type") == "file" for f in files) and not any(
                f.get("type") == "dir" for f in files):
            return "insufficient_evidence"
        return "input_error" if not case_id else "partial"
    if not any(f.get("type") == "file" for f in files):
        return "insufficient_evidence"
    return "partial"


def build_result(case_id, status, contract, files, errors, degradations):
    result = {
        "case_id": case_id,
        "status": status,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": errors,
        "degradations": degradations,
        "runtime": {},
        "limitations": [],
        "change_log": [],
        "computed": {},
        "artifacts": {},
    }
    merge_contract(result, contract)
    return result


def log_entry(entries, step, detail):
    entries.append({"time": now_iso(), "actor": "skill", "step": step, "detail": detail})


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def full_run(args):
    input_root = os.path.abspath(args.input_root or "/input")
    output_dir = os.path.abspath(args.output_dir or "/app/output")
    work_dir = os.path.abspath(args.work_dir or "/app/work")
    case_json_path = os.path.abspath(args.case_json) if args.case_json else os.path.join(input_root, "case.json")
    task_path = os.path.abspath(args.task_path) if args.task_path else os.path.join(input_root, "task.md")
    result_path = os.path.abspath(args.result) if args.result else os.path.join(output_dir, "result.json")

    started = time.time()
    errors = []
    degradations = []
    change_log = []

    for d in (output_dir, work_dir):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as exc:
            errors.append({"path": d, "kind": "mkdir_failed", "detail": str(exc)})

    log_entry(change_log, "start", "deterministic pipeline starting")

    # 1. control inputs
    case_data = read_json(case_json_path, errors) if os.path.isfile(case_json_path) else None
    if case_data is None and not os.path.isfile(case_json_path):
        errors.append({"path": case_json_path, "kind": "missing",
                       "detail": "case.json not found"})
    contract = extract_contract(case_data or {}, errors)
    case_id = detect_case_id(case_data or {}, task_path)

    # 2. inventory (skip control files, keep the rest)
    skip = {os.path.basename(case_json_path), os.path.basename(task_path)}
    files, walk_errors = inventory_tree(input_root, skip)
    errors.extend(walk_errors)

    regular = [f for f in files if f.get("type") == "file"]
    dirs = [f for f in files if f.get("type") == "dir"]

    # 3. dedup / version / alias / notes
    dup_groups = group_duplicates(regular)
    version_groups = group_versions(regular, input_root)
    hard_groups, symlinks = group_aliases(files)
    version_notes = parse_version_notes(regular, input_root)

    # 4. catalogs + reconciliation
    catalogs, records = parse_catalogs(regular, input_root, errors)
    reconciliation = reconcile(records, regular)

    # 5. index + answers
    index = build_index(files)
    answers = compute_answers(files, dirs, dup_groups, hard_groups, symlinks,
                              version_groups, version_notes, reconciliation)

    status = decide_status(case_data, case_id, files, errors)
    result = build_result(case_id, status, contract, files, errors, degradations)
    result["computed"].update({
        "summary": answers,
        "inventory": {"root": input_root, "skip_names": sorted(skip, key=stable_key)},
        "files": files,
        "duplicate_groups": dup_groups,
        "version_candidates": version_groups,
        "version_notes": version_notes,
        "aliases": {"hardlink_groups": hard_groups, "symlinks": symlinks},
        "reconciliation": reconciliation,
        "catalogs": catalogs,
        "index": index,
        "answers": answers,
    })

    # 6. write editable artifacts
    artifacts = {
        "library_manifest.csv": os.path.join(output_dir, "library_manifest.csv"),
        "directory_tree.txt": os.path.join(output_dir, "directory_tree.txt"),
        "dedup_version.json": os.path.join(output_dir, "dedup_version.json"),
        "recovery_report.json": os.path.join(output_dir, "recovery_report.json"),
        "library_index.json": os.path.join(output_dir, "library_index.json"),
        "library_manifest.md": os.path.join(output_dir, "library_manifest.md"),
        "operation_log.jsonl": os.path.join(output_dir, "operation_log.jsonl"),
    }
    write_failures = []

    def safe_write(name, writer):
        try:
            writer(artifacts[name])
        except OSError as exc:
            write_failures.append({"artifact": name, "detail": str(exc)})
            errors.append({"path": artifacts[name], "kind": "write_failed",
                           "detail": str(exc)})

    safe_write("library_manifest.csv", lambda p: write_manifest_csv(files, p))
    safe_write("directory_tree.txt", lambda p: write_directory_tree(files, p))
    safe_write("dedup_version.json", lambda p: write_json(p, {
        "duplicate_groups": dup_groups,
        "version_candidates": version_groups,
        "version_notes": version_notes,
        "aliases": {"hardlink_groups": hard_groups, "symlinks": symlinks},
        "heuristic": True,
    }))
    safe_write("recovery_report.json", lambda p: write_json(p, {
        "reconciliation": reconciliation,
        "catalogs": catalogs,
        "errors": errors,
        "degradations": degradations,
    }))
    safe_write("library_index.json", lambda p: write_json(p, index))

    # record artifact paths relative to output dir for portability
    result["artifacts"] = {
        k: os.path.basename(v) for k, v in artifacts.items()
        if os.path.isfile(v)
    }

    safe_write("library_manifest.md", lambda p: write_library_manifest_md(result, files, p))

    # 7. operation log (deterministic content, only time varies)
    log_entry(change_log, "inventory", "%d entries inventoried" % len(files))
    log_entry(change_log, "dedup", "%d duplicate groups" % len(dup_groups))
    log_entry(change_log, "version", "%d version candidate groups" % len(version_groups))
    log_entry(change_log, "alias", "%d hardlink groups, %d symlinks"
              % (len(hard_groups), len(symlinks)))
    log_entry(change_log, "reconcile", "matches=%d missing=%d mismatches=%d orphaned=%d"
              % (reconciliation["matches"], len(reconciliation["missing"]),
                 len(reconciliation["mismatches"]), len(reconciliation["orphaned"])))
    result["change_log"] = change_log

    if write_failures:
        result["degradations"] = write_failures

    result["runtime"] = {
        "started_at": now_iso(),
        "elapsed_seconds": round(time.time() - started, 3),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "argv": sys.argv[1:],
    }

    # 8. write result.json; if this itself fails, exit 2 but keep prior files
    try:
        write_json(result_path, result)
    except OSError as exc:
        # last-resort minimal result next to output dir
        fallback = os.path.join(output_dir, "result.fallback.json")
        try:
            write_json(fallback, {
                "case_id": case_id, "status": "input_error",
                "errors": [{"path": result_path, "kind": "write_failed", "detail": str(exc)}],
                "change_log": change_log,
            })
        except OSError:
            pass
        print("finalize: failed to write result.json: %s" % exc, file=sys.stderr)
        return 2

    # append operation log after result.json succeeds
    try:
        with open(artifacts["operation_log.jsonl"], "w", encoding="utf-8", newline="\n") as fh:
            for e in change_log:
                fh.write(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n")
        result["artifacts"]["operation_log.jsonl"] = os.path.basename(
            artifacts["operation_log.jsonl"])
    except OSError as exc:
        errors.append({"path": artifacts["operation_log.jsonl"], "kind": "write_failed",
                       "detail": str(exc)})

    print("finalize: %d files, %d dup groups, %d version candidates, "
          "%d broken symlinks, %d reconcile mismatches -> %s"
          % (len(regular), len(dup_groups), len(version_groups),
             len([s for s in symlinks if s.get("broken")]),
             len(reconciliation["mismatches"]), result_path))
    return 0


def skeleton_only(args):
    input_root = os.path.abspath(args.input_root or "/input")
    output_dir = os.path.abspath(args.output_dir or "/app/output")
    case_json_path = os.path.abspath(args.case_json) if args.case_json else os.path.join(input_root, "case.json")
    task_path = os.path.abspath(args.task_path) if args.task_path else os.path.join(input_root, "task.md")
    result_path = os.path.abspath(args.result) if args.result else os.path.join(output_dir, "result.json")

    errors = []
    case_data = read_json(case_json_path, errors) if os.path.isfile(case_json_path) else None
    if case_data is None and not os.path.isfile(case_json_path):
        errors.append({"path": case_json_path, "kind": "missing", "detail": "case.json not found"})
    contract = extract_contract(case_data or {}, errors)
    case_id = detect_case_id(case_data or {}, task_path)
    status = "input_error" if not case_id else "partial"

    result = build_result(case_id, status, contract, [], errors, [])
    result["change_log"] = [{
        "time": now_iso(), "actor": "skill", "step": "skeleton",
        "detail": "partial skeleton written before analysis (fail-safe)"}]
    result["runtime"] = {"started_at": now_iso(), "elapsed_seconds": 0,
                         "python": sys.version.split()[0], "platform": sys.platform,
                         "argv": sys.argv[1:]}

    try:
        os.makedirs(output_dir, exist_ok=True)
        write_json(result_path, result)
    except OSError as exc:
        print("skeleton: write failed: %s" % exc, file=sys.stderr)
        return 2
    print("skeleton: -> %s" % result_path)
    return 0


def update(args):
    result_path = os.path.abspath(args.result) if args.result else os.path.join(
        os.path.abspath(args.output_dir or "/app/output"), "result.json")
    patch_path = os.path.abspath(args.update)
    errors = []
    base = read_json(result_path, errors)
    if base is None:
        print("update: cannot read result.json", file=sys.stderr)
        return 1
    patch = read_json(patch_path, errors)
    if patch is None:
        print("update: cannot read patch file", file=sys.stderr)
        return 1
    deep_merge(base, patch)
    base.setdefault("change_log", []).append({
        "time": now_iso(), "actor": "skill", "step": "update",
        "detail": "merged patch %s" % os.path.basename(patch_path)})
    write_json(result_path, base)
    print("update: merged %s -> %s" % (patch_path, result_path))
    return 0


def check(args):
    result_path = os.path.abspath(args.check) if args.check else os.path.join(
        os.path.abspath(args.output_dir or "/app/output"), "result.json")
    case_json_path = os.path.abspath(args.case_json) if args.case_json else os.path.join(
        os.path.abspath(args.input_root or "/input"), "case.json")
    errors = []
    result = read_json(result_path, errors)
    issues = []
    if result is None:
        print("check: result.json unreadable")
        return 1
    if not result.get("case_id"):
        issues.append("case_id empty")
    if result.get("status") not in ALLOWED_STATUS:
        issues.append("invalid status %r" % result.get("status"))
    case_data = read_json(case_json_path, errors) if os.path.isfile(case_json_path) else None
    contract = extract_contract(case_data or {}, errors)
    for field in contract_fields(contract):
        if field not in result:
            issues.append("missing contract field %r" % field)
    missing_artifacts = [k for k in ("sources", "uncertainty", "change_log")
                         if k not in result]
    issues.extend("missing %s" % k for k in missing_artifacts)

    if issues:
        print("check: FAIL")
        for i in issues:
            print("  - %s" % i)
        return 1
    print("check: OK  case_id=%s status=%s" % (result["case_id"], result["status"]))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="core_impl.py",
        description="Deterministic document-library repair pipeline (stdlib only)")
    p.add_argument("--input", "--input-root", dest="input_root")
    p.add_argument("--output", "--output-dir", dest="output_dir")
    p.add_argument("--work", "--work-dir", dest="work_dir")
    p.add_argument("--case-json")
    p.add_argument("--task-path")
    p.add_argument("--result")
    p.add_argument("--skeleton-only", action="store_true")
    p.add_argument("--update", metavar="PATCH.json")
    p.add_argument("--check", metavar="RESULT.json")
    return p


def main(argv):
    args = build_parser().parse_args(argv)
    if args.check:
        return check(args)
    if args.update:
        return update(args)
    if args.skeleton_only:
        return skeleton_only(args)
    return full_run(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
