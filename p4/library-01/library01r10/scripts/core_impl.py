#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — deterministic implementation of the library-repair skill.

Architecture (deliberately different from a single linear pipeline):

  Layer 1  crawl()   : pure observation.  Walks the input tree and emits only
                       observed facts (paths, stat fields, content hashes, link
                       targets).  It never edits sources and never writes final
                       artifacts.
  Layer 2  analyze() : lifts the crawl into one intermediate representation (IR):
                       duplicate groups, version candidates, aliases, catalog
                       parses, reconciliation, searchable index and mechanical
                       answers.  Every derived fact carries provenance.
  Layer 3  render()  : pure projection.  Reads the IR and writes the editable
                       artifacts plus result.json.  The IR is validated *before*
                       any artifact is written, so a broken stage cannot leave a
                       half-written deliverable behind.

Validation order (re-runnable gates):
  skeleton -> crawl -> IR-completeness check -> analyze -> IR-integrity check
  -> render -> final --check.

Only the Python standard library is used.
"""

import csv
import difflib
import hashlib
import io
import json
import os
import re
import sqlite3
import stat as stat_mod
import sys
import time
import unicodedata
from collections import OrderedDict, defaultdict

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

CONTRACT_KEYS = (
    "output_contract",
    "output",
    "result_schema",
    "contract",
    "outputSchema",
    "result_schema",
)

RESERVED_KEYS = (
    "case_id",
    "status",
    "sources",
    "uncertainty",
    "pending_human",
    "change_log",
    "errors",
    "degradations",
    "runtime",
    "limitations",
    "computed",
    "artifacts",
    "output_dir",
)

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

DEFAULT_HASH_LIMIT = 2 * 1024 * 1024  # 2 MiB: files larger than this are not content-hashed
CHUNK = 65536
HEAD_BYTES = 4096

ARTIFACTS = OrderedDict(
    (
        ("manifest", "library_manifest.csv"),
        ("tree", "directory_tree.txt"),
        ("dedup_version", "dedup_version.json"),
        ("recovery", "recovery_report.json"),
        ("index", "library_index.json"),
        ("report", "library_manifest.md"),
        ("operation_log", "operation_log.jsonl"),
    )
)

EXT_MIME = {
    "pdf": "application/pdf",
    "json": "application/json",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "bib": "text/x-bibtex",
    "ris": "text/x-ris",
    "txt": "text/plain",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "html": "text/html",
    "htm": "text/html",
    "xml": "application/xml",
    "sqlite": "application/x-sqlite3",
    "db": "application/x-sqlite3",
    "sqlite3": "application/x-sqlite3",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "zip": "application/zip",
    "tar": "application/x-tar",
    "gz": "application/gzip",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "svg": "image/svg+xml",
    "py": "text/x-python",
    "r": "text/x-r",
    "tex": "text/x-tex",
    "ipynb": "application/x-ipynb+json",
}

CATALOG_EXTENSIONS = {".json", ".csv", ".bib", ".ris", ".sqlite", ".db", ".sqlite3"}
CATALOG_NAME_HINT = re.compile(
    r"(manifest|catalog|index|library|inventory|list|records|zotero|reference)", re.I
)

# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def stable_path(p):
    """Normalize a relative path to forward slashes for cross-platform output."""
    return p.replace("\\", "/")


def ext_of(name):
    e = os.path.splitext(name)[1].lower()
    return e.lstrip(".") if e else None


def read_text_any(path, limit=8 * 1024 * 1024):
    """Read text with utf-8-sig first, fall back to latin-1; never raise."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(limit)
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("latin-1", errors="replace")


def load_json_file(path):
    """Tolerant JSON load. Returns (obj, error_string)."""
    if not os.path.isfile(path):
        return None, "file_missing"
    text = read_text_any(path)
    if text is None:
        return None, "unreadable"
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, "bad_json: %s" % e


def deep_get(d, keys, default=None):
    for k in keys:
        if isinstance(d, dict) and k in d:
            return d[k]
    return default


def pick(d, keys):
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] not in (None, "", []):
            return d[k]
    return None


def norm_key(s):
    """Normalize a key for fuzzy matching: lower-case alnum only."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def read_head(path, n=HEAD_BYTES):
    try:
        with open(path, "rb") as fh:
            return fh.read(n)
    except OSError:
        return b""


def is_binary(head):
    return b"\x00" in head[:1024]


def guess_mime(name, head):
    ext = ext_of(name)
    if ext in EXT_MIME:
        return EXT_MIME[ext]
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"SQLite format 3\x00"):
        return "application/x-sqlite3"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"PK\x03\x04"):
        return "application/zip"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if head.startswith(b"\x1f\x8b"):
        return "application/gzip"
    if head[:4] == b"\x7fELF":
        return "application/x-elf"
    return "application/octet-stream"


def hash_file(path):
    """Stream SHA-256 of a regular file. Returns (hexdigest_or_None, error_or_None)."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest(), None
    except OSError:
        return None, "unreadable"


def iso_ts(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts))


def safe_json_dumps(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


def write_json_atomic(path, obj):
    """Write JSON via a temp file then replace, so a crash cannot leave a partial file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(safe_json_dumps(obj))
        fh.write("\n")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Layer 1: crawl — pure observation
# --------------------------------------------------------------------------- #


def make_entry_record(full, input_root):
    """Inspect one filesystem entry with lstat; returns (record, errors)."""
    rel = stable_path(os.path.relpath(full, input_root))
    name = os.path.basename(full)
    rec = {
        "rel_path": rel,
        "abs_path": full,
        "name": name,
        "depth": rel.count("/") if rel not in (".", "") else 0,
        "kind": None,
        "size": None,
        "mtime": None,
        "ctime": None,
        "mode": None,
        "permissions": None,
        "uid": None,
        "gid": None,
        "inode": None,
        "nlink": None,
        "sha256": None,
        "sha256_status": "not_hashed",
        "extension": ext_of(name),
        "mime_hint": None,
        "is_binary": None,
        "target": None,
        "is_hidden": name.startswith("."),
    }
    errors = []
    try:
        st = os.lstat(full)
    except OSError as e:
        errors.append(
            {"item": rel, "error": "unreadable", "detail": str(e)}
        )
        rec["kind"] = "unreadable"
        rec["sha256_status"] = "unreadable"
        return rec, errors

    mode = st.st_mode
    rec.update(
        mode=oct(stat_mod.S_IMODE(mode)),
        permissions=oct(stat_mod.S_IMODE(mode)),
        uid=st.st_uid,
        gid=st.st_gid,
        inode=st.st_ino,
        nlink=st.st_nlink,
        mtime=iso_ts(st.st_mtime),
        ctime=iso_ts(st.st_ctime),
    )

    if stat_mod.S_ISLNK(mode):
        try:
            target = os.readlink(full)
        except OSError:
            target = None
        rec["target"] = target
        try:
            os.stat(full)
            rec["kind"] = "symlink"
        except OSError:
            rec["kind"] = "broken_symlink"
        rec["sha256_status"] = "symlink"
        return rec, errors

    if stat_mod.S_ISREG(mode):
        rec["kind"] = "file"
        rec["size"] = st.st_size
        head = read_head(full)
        rec["is_binary"] = is_binary(head)
        rec["mime_hint"] = guess_mime(name, head)
        if st.st_size > DEFAULT_HASH_LIMIT:
            rec["sha256_status"] = "too_large"
        else:
            digest, err = hash_file(full)
            if err:
                rec["sha256_status"] = err
                errors.append({"item": rel, "error": "hash_failed", "detail": err})
            else:
                rec["sha256"] = digest
                rec["sha256_status"] = "hashed"
        return rec, errors

    # fifo / socket / device etc.
    rec["kind"] = "other"
    rec["sha256_status"] = "non_regular"
    return rec, errors


def crawl(input_root):
    """Walk the input tree deterministically. Returns (files, dirs, errors)."""
    files, dirs, errors = [], [], []
    if not os.path.isdir(input_root):
        errors.append(
            {
                "item": input_root,
                "error": "input_root_missing",
                "detail": "input root is not an existing directory",
            }
        )
        return files, dirs, errors

    for dirpath, dirnames, filenames in os.walk(input_root, topdown=True, followlinks=False):
        dirnames.sort()
        filenames.sort()
        rel_dir = stable_path(os.path.relpath(dirpath, input_root))

        # Directories listed here are not descended into (followlinks=False), so
        # symlinked directories must be classified explicitly.
        for dn in list(dirnames):
            full = os.path.join(dirpath, dn)
            rel = stable_path(os.path.relpath(full, input_root))
            try:
                st = os.lstat(full)
            except OSError as e:
                errors.append({"item": rel, "error": "unreadable", "detail": str(e)})
                continue
            if stat_mod.S_ISLNK(st.st_mode):
                rec, errs = make_entry_record(full, input_root)
                files.append(rec)
                errors.extend(errs)
                continue
            # Real directory.
            empty = False
            try:
                empty = len(os.listdir(full)) == 0
            except OSError as e:
                errors.append({"item": rel, "error": "listdir_failed", "detail": str(e)})
            dirs.append(
                {
                    "rel_path": rel,
                    "name": dn,
                    "depth": rel.count("/"),
                    "kind": "dir",
                    "empty": empty,
                    "mode": oct(stat_mod.S_IMODE(st.st_mode)),
                    "permissions": oct(stat_mod.S_IMODE(st.st_mode)),
                    "uid": st.st_uid,
                    "gid": st.st_gid,
                    "mtime": iso_ts(st.st_mtime),
                    "ctime": iso_ts(st.st_ctime),
                    "is_hidden": dn.startswith("."),
                }
            )

        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rec, errs = make_entry_record(full, input_root)
            if rec is not None:
                files.append(rec)
            errors.extend(errs)

    files.sort(key=lambda r: r["rel_path"])
    dirs.sort(key=lambda r: r["rel_path"])
    return files, dirs, errors


# --------------------------------------------------------------------------- #
# Layer 2: analyze — build the intermediate representation
# --------------------------------------------------------------------------- #

# ---- dedup ----


def build_duplicate_groups(files):
    by_hash = defaultdict(list)
    for f in files:
        if f.get("kind") == "file" and f.get("sha256") and f.get("sha256_status") == "hashed":
            by_hash[f["sha256"]].append(f)
    groups = []
    for h, lst in sorted(by_hash.items()):
        if len(lst) < 2:
            continue
        lst = sorted(lst, key=lambda r: r["rel_path"])
        groups.append(
            {
                "sha256": h,
                "count": len(lst),
                "size": lst[0].get("size"),
                "paths": [r["rel_path"] for r in lst],
            }
        )
    return groups


# ---- version candidates ----


VERSION_RE = (
    re.compile(r"\b(?:v|ver|version|rev|revision|r)[\-_. ]*\d+(?:\.\d+)*\b", re.I),
    re.compile(r"\b(?:final|draft|copy|backup|old|new|clean|tmp|temp|edited|updated|latest|corr|corrected)\b", re.I),
    re.compile(r"\b\d{4}[-_/]\d{1,2}[-_/]\d{1,2}\b"),
    re.compile(r"\b\d{8}\b"),
    re.compile(r"[\[(](?:\d+|\d+(?:\.\d+)*|copy|final|draft|clean)[\])]", re.I),
)


def normalize_stem(name):
    base = os.path.splitext(name)[0]
    s = unicodedata.normalize("NFKC", base).lower()
    s = re.sub(r"[\s_\-–—]+", " ", s)
    for pat in VERSION_RE:
        s = pat.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def text_similarity(f1, f2, max_bytes=128 * 1024):
    try:
        with open(f1["abs_path"], "rb") as a, open(f2["abs_path"], "rb") as b:
            ta = a.read(max_bytes).decode("utf-8", errors="replace")
            tb = b.read(max_bytes).decode("utf-8", errors="replace")
    except OSError:
        return None
    if not ta and not tb:
        return 1.0
    return round(difflib.SequenceMatcher(None, ta, tb).ratio(), 4)


def build_version_candidates(files):
    by_key = defaultdict(list)
    for f in files:
        if f.get("kind") != "file":
            continue
        key = (normalize_stem(f["name"]), f.get("extension"))
        by_key[key].append(f)

    candidates = []
    for key in sorted(by_key.keys()):
        lst = by_key[key]
        hashes = {f.get("sha256") for f in lst if f.get("sha256")}
        if len(lst) < 2 or len(hashes) < 2:
            continue
        ordered = sorted(lst, key=lambda r: (r.get("mtime") or "", r["rel_path"]))
        chain = []
        for f in ordered:
            chain.append(
                {
                    "rel_path": f["rel_path"],
                    "sha256": f.get("sha256"),
                    "mtime": f.get("mtime"),
                    "size": f.get("size"),
                }
            )
        # Pairwise content similarity along the mtime-ordered chain.
        diffs = []
        for a, b in zip(chain, chain[1:]):
            fa = next((x for x in lst if x["rel_path"] == a["rel_path"]), None)
            fb = next((x for x in lst if x["rel_path"] == b["rel_path"]), None)
            sim = None
            if fa is not None and fb is not None:
                sim = text_similarity(fa, fb)
            diffs.append(
                {
                    "from": a["rel_path"],
                    "to": b["rel_path"],
                    "similarity": sim,
                    "method": "text-ratio" if sim is not None else "unavailable",
                }
            )
        candidates.append(
            {
                "stem": key[0],
                "extension": key[1],
                "count": len(chain),
                "distinct_hashes": len(hashes),
                "chain": chain,
                "diffs": diffs,
                "heuristic": True,
                "note": "candidate version chain; ordering by mtime, content identity by SHA-256",
            }
        )
    return candidates


# ---- aliases ----


def build_aliases(files):
    symlinks = [f for f in files if f.get("kind") in ("symlink", "broken_symlink")]
    broken = [f for f in symlinks if f.get("kind") == "broken_symlink"]

    by_inode = defaultdict(list)
    for f in files:
        if f.get("kind") == "file" and f.get("nlink", 1) and f.get("nlink", 1) > 1:
            by_inode[f.get("inode")].append(f)
    hardlinks = []
    for inode, lst in sorted(by_inode.items(), key=lambda kv: str(kv[0])):
        if len(lst) < 2:
            continue
        lst = sorted(lst, key=lambda r: r["rel_path"])
        hardlinks.append({"inode": inode, "nlink": lst[0].get("nlink"), "paths": [r["rel_path"] for r in lst]})

    return {
        "symlinks": [f for f in symlinks],
        "broken_symlink_count": len(broken),
        "hardlink_groups": hardlinks,
    }


# ---- catalog parsing ----


def parse_json_catalog(text, locator):
    records, meta = [], {}
    try:
        data = json.loads(text)
    except ValueError as e:
        return records, meta, "bad_json: %s" % e
    if isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, dict):
                records.append((item, "%s#item-%d" % (locator, i)))
        return records, meta, None
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, list):
                for i, item in enumerate(v):
                    if isinstance(item, dict):
                        records.append((item, "%s#%s-%d" % (locator, k, i)))
            else:
                meta[k] = v
        return records, meta, None
    return records, meta, "unexpected_json_shape"


def parse_csv_catalog(text, locator):
    records = []
    try:
        reader = csv.reader(io.StringIO(text))
        rows = [r for r in reader if any(cell.strip() for cell in r)]
    except csv.Error as e:
        return records, "bad_csv: %s" % e
    if not rows:
        return records, None
    header = [c.strip() for c in rows[0]]
    if not header or all(not h for h in header):
        return records, "empty_csv_header"
    for i, row in enumerate(rows[1:]):
        d = {}
        for j, h in enumerate(header):
            d[h or ("col_%d" % j)] = row[j] if j < len(row) else ""
        records.append((d, "%s#row-%d" % (locator, i + 1)))
    return records, None


def parse_bibtex_catalog(text, locator):
    records = []
    entry_re = re.compile(r"@(\w+)\s*\{\s*([^,]*)\s*,", re.I)
    field_re = re.compile(r"(\w+)\s*=\s*[{\"]([^}\"]*)[}\"]", re.S)
    for m in entry_re.finditer(text):
        etype, key = m.group(1), m.group(2).strip()
        body_start = m.end()
        nxt = entry_re.search(text, body_start)
        body = text[body_start: nxt.start() if nxt else len(text)]
        fields = {}
        for fm in field_re.finditer(body):
            fields[fm.group(1).lower()] = fm.group(2).strip()
        fields["_bibtex_type"] = etype
        fields["_bibtex_key"] = key
        records.append((fields, "%s#%s" % (locator, key or etype)))
    return records, None


def parse_ris_catalog(text, locator):
    records = []
    cur = None
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        if line.strip().upper() == "ER  -":
            if cur is not None:
                records.append((cur, "%s#record-%d" % (locator, len(records))))
            cur = None
            continue
        if len(line) >= 6 and line[2:6] == "  - ":
            tag = line[:2].upper()
            value = line[6:].strip()
            if cur is None:
                cur = {}
            if tag == "TY":
                cur["_ris_type"] = value
            else:
                cur[tag] = value
        elif len(line) >= 2 and line[1:4] == "  - ":
            tag = line[:1].upper()
            value = line[4:].strip()
            if cur is None:
                cur = {}
            cur[tag] = value
    if cur is not None:
        records.append((cur, "%s#record-%d" % (locator, len(records))))
    return records, None


def parse_sqlite_catalog(path, locator):
    records = []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    except sqlite3.Error as e:
        return records, "sqlite_open_error: %s" % e
    try:
        cur = con.cursor()
        tables = [
            r[0]
            for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        for t in tables:
            try:
                cols = [c[1] for c in cur.execute("PRAGMA table_info(%s)" % t).fetchall()]
                rows = cur.execute("SELECT * FROM %s LIMIT 1000" % t).fetchall()
            except sqlite3.Error as e:
                return records, "sqlite_query_error(%s): %s" % (t, e)
            for i, row in enumerate(rows):
                d = {"_table": t}
                for j, c in enumerate(cols):
                    d[c] = row[j] if j < len(row) else None
                records.append((d, "%s#%s-%d" % (locator, t, i)))
    finally:
        try:
            con.close()
        except Exception:
            pass
    return records, None


def discover_catalogs(files, input_root):
    """Identify and parse library catalogs among the regular files."""
    parses, degradations = [], []
    for f in files:
        if f.get("kind") != "file":
            continue
        ext = f.get("extension")
        name = f.get("name") or ""
        is_catalog = ext in CATALOG_EXTENSIONS or bool(CATALOG_NAME_HINT.search(name))
        if not is_catalog:
            continue
        locator = f["rel_path"]
        entry = {
            "rel_path": locator,
            "format": ext,
            "records": [],
            "meta": {},
            "error": None,
        }
        if ext in ("sqlite", "db", "sqlite3"):
            recs, err = parse_sqlite_catalog(f["abs_path"], locator)
        else:
            text = read_text_any(f["abs_path"])
            if text is None:
                recs, err = [], "unreadable"
            elif ext == "json":
                recs, meta, err = parse_json_catalog(text, locator)
                entry["meta"] = meta
            elif ext == "csv":
                recs, err = parse_csv_catalog(text, locator)
            elif ext == "bib":
                recs, err = parse_bibtex_catalog(text, locator)
            elif ext == "ris":
                recs, err = parse_ris_catalog(text, locator)
            else:
                recs, err = [], "unsupported_catalog_format"
        entry["records"] = [r for r, _ in recs]
        entry["record_locators"] = [l for _, l in recs]
        entry["error"] = err
        if err:
            degradations.append(
                {"item": locator, "error": err, "detail": "catalog parse degraded"}
            )
        parses.append(entry)
    parses.sort(key=lambda e: e["rel_path"])
    return parses, degradations


# ---- reconciliation ----


def extract_catalog_path(rec):
    for k in (
        "path",
        "file",
        "filename",
        "location",
        "rel_path",
        "relative_path",
        "filepath",
        "url",
        "name",
        "id",
    ):
        v = deep_get(rec, [k], None)
        if v is None:
            continue
        s = str(v)
        s = s.replace("\\", "/")
        if s.startswith("file://"):
            s = s[len("file://"):]
        if s:
            return s
    return None


def reconcile(catalog_parses, files):
    file_by_path = {f["rel_path"]: f for f in files if f.get("kind") == "file"}
    file_by_name = defaultdict(list)
    for f in files:
        if f.get("kind") == "file":
            file_by_name[f.get("name", "")].append(f)

    matches, mismatches, missing, orphaned = [], [], [], []
    referenced = set()
    for cat in catalog_parses:
        for i, rec in enumerate(cat.get("records", [])):
            path = extract_catalog_path(rec)
            locator = (cat.get("record_locators") or [None])[i] if i < len(cat.get("record_locators") or []) else cat["rel_path"]
            if not path:
                continue
            normalized = path.lstrip("./")
            f = file_by_path.get(normalized)
            if f is None and normalized not in file_by_path:
                # fall back to basename match
                base = os.path.basename(normalized)
                cands = file_by_name.get(base, [])
                f = cands[0] if len(cands) == 1 else None
            referenced.add(normalized)
            entry = {
                "catalog": cat["rel_path"],
                "catalog_path": path,
                "locator": locator,
                "catalog_record": rec,
            }
            if f is None:
                missing.append(entry)
                continue
            entry["actual_path"] = f["rel_path"]
            cat_hash = deep_get(rec, ["sha256", "hash", "checksum", "md5"], None)
            if cat_hash and f.get("sha256") and str(cat_hash) != f["sha256"]:
                entry["catalog_hash"] = str(cat_hash)
                entry["actual_hash"] = f["sha256"]
                mismatches.append(entry)
            else:
                matches.append(entry)

    for f in files:
        if f.get("kind") == "file" and f["rel_path"] not in referenced:
            orphaned.append(f["rel_path"])

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


# ---- version notes from task.md ----


def parse_version_notes(task_text):
    if not task_text:
        return []
    notes = []
    note_re = re.compile(
        r"(版本|version|修订|revision|替代|替代品|supersed|final|draft|v\d+(?:\.\d+)*)",
        re.I,
    )
    for ln, line in enumerate(task_text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if note_re.search(stripped):
            notes.append(
                {"line": ln, "quote": stripped[:400], "locator": "task.md#L%d" % ln}
            )
    return notes


# ---- searchable index & mechanical answers ----


def build_index(files, dirs):
    by_extension = defaultdict(list)
    by_hash = defaultdict(list)
    by_keyword = defaultdict(list)
    by_kind = defaultdict(list)
    for f in files:
        by_kind[f.get("kind")].append(f["rel_path"])
        if f.get("extension"):
            by_extension[f["extension"]].append(f["rel_path"])
        if f.get("sha256"):
            by_hash[f["sha256"]].append(f["rel_path"])
        tokens = set(
            re.findall(r"[a-zA-Z0-9\u4e00-\u9fff_]+", (f.get("name") or "").lower())
        )
        for t in tokens:
            by_keyword[t].append(f["rel_path"])
    return {
        "by_extension": {k: sorted(v) for k, v in sorted(by_extension.items())},
        "by_hash": {k: sorted(v) for k, v in sorted(by_hash.items())},
        "by_keyword": {k: sorted(set(v)) for k, v in sorted(by_keyword.items())},
        "by_kind": {k: sorted(v) for k, v in sorted(by_kind.items())},
        "dir_count": len(dirs),
    }


def search_index(index, query):
    q = norm_key(query)
    hits = []
    for tok, paths in index.get("by_keyword", {}).items():
        if q and (q in tok or tok in q):
            hits.extend(paths)
    return sorted(set(hits))


def mechanical_answers(files, dirs, dup_groups, version_candidates, aliases, recon, index):
    total_size = sum(f.get("size") or 0 for f in files if f.get("kind") == "file")
    ext_counts = {}
    for ext, paths in index.get("by_extension", {}).items():
        ext_counts[ext] = len(paths)
    answers = [
        {
            "question": "总文件数（regular file）",
            "answer": sum(1 for f in files if f.get("kind") == "file"),
            "evidence": {"locator": "inventory", "quote": "count of kind=file"},
        },
        {
            "question": "目录数",
            "answer": len(dirs),
            "evidence": {"locator": "inventory", "quote": "count of directories"},
        },
        {
            "question": "文件总体积（字节）",
            "answer": total_size,
            "evidence": {"locator": "inventory", "quote": "sum of file sizes"},
        },
        {
            "question": "重复文件组数",
            "answer": len(dup_groups),
            "evidence": {"locator": "dedup_version", "quote": "SHA-256 duplicate groups"},
        },
        {
            "question": "重复文件总数（去重后可节省的副本数）",
            "answer": sum(g["count"] - 1 for g in dup_groups),
            "evidence": {"locator": "dedup_version", "quote": "sum(count-1)"},
        },
        {
            "question": "版本候选链数",
            "answer": len(version_candidates),
            "evidence": {"locator": "dedup_version", "quote": "version_candidates"},
        },
        {
            "question": "断链符号链接数",
            "answer": aliases.get("broken_symlink_count", 0),
            "evidence": {"locator": "dedup_version", "quote": "broken_symlink_count"},
        },
        {
            "question": "库目录缺失条目数",
            "answer": recon["counts"]["missing"],
            "evidence": {"locator": "recovery_report", "quote": "reconciliation missing"},
        },
        {
            "question": "库目录哈希不符条目数",
            "answer": recon["counts"]["mismatches"],
            "evidence": {"locator": "recovery_report", "quote": "reconciliation mismatches"},
        },
        {
            "question": "孤儿文件数（目录未引用）",
            "answer": recon["counts"]["orphaned"],
            "evidence": {"locator": "recovery_report", "quote": "reconciliation orphaned"},
        },
        {
            "question": "按扩展名计数",
            "answer": ext_counts,
            "evidence": {"locator": "library_index", "quote": "by_extension"},
        },
    ]
    return answers


def build_summary(files, dirs, dup_groups, version_candidates, aliases, recon, catalog_parses):
    return {
        "file_count": sum(1 for f in files if f.get("kind") == "file"),
        "dir_count": len(dirs),
        "symlink_count": len(aliases.get("symlinks", [])),
        "broken_symlink_count": aliases.get("broken_symlink_count", 0),
        "hardlink_group_count": len(aliases.get("hardlink_groups", [])),
        "duplicate_group_count": len(dup_groups),
        "duplicate_excess_files": sum(g["count"] - 1 for g in dup_groups),
        "version_candidate_count": len(version_candidates),
        "catalog_count": len(catalog_parses),
        "total_bytes": sum(f.get("size") or 0 for f in files if f.get("kind") == "file"),
        "file_kinds": {
            k: sum(1 for f in files if f.get("kind") == k)
            for k in sorted({f.get("kind") for f in files if f.get("kind")})
        },
    }


# --------------------------------------------------------------------------- #
# Layer 3: render — projection of the IR to artifacts
# --------------------------------------------------------------------------- #


def write_manifest_csv(output_dir, files, dirs):
    path = os.path.join(output_dir, ARTIFACTS["manifest"])
    cols = [
        "rel_path",
        "name",
        "kind",
        "size",
        "sha256",
        "sha256_status",
        "mtime",
        "ctime",
        "permissions",
        "uid",
        "gid",
        "inode",
        "nlink",
        "extension",
        "mime_hint",
        "is_binary",
        "target",
        "depth",
        "is_hidden",
    ]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for d in dirs:
            w.writerow(
                [
                    d["rel_path"], d["name"], d["kind"], "", "", "", d["mtime"], d["ctime"],
                    d.get("permissions", ""), d.get("uid", ""), d.get("gid", ""), "", "",
                    "", "", "", "", d["depth"], d.get("is_hidden", False),
                ]
            )
        for f in files:
            w.writerow(
                [
                    f["rel_path"], f["name"], f["kind"], f.get("size", ""), f.get("sha256", ""),
                    f.get("sha256_status", ""), f.get("mtime", ""), f.get("ctime", ""),
                    f.get("permissions", ""), f.get("uid", ""), f.get("gid", ""),
                    f.get("inode", ""), f.get("nlink", ""), f.get("extension", ""),
                    f.get("mime_hint", ""), f.get("is_binary", ""), f.get("target", ""),
                    f.get("depth", ""), f.get("is_hidden", False),
                ]
            )
    return ARTIFACTS["manifest"]


def _render_tree_node(node, prefix, lines):
    names = [k for k in node.keys() if not k.startswith("__")]
    names.sort()
    for i, name in enumerate(names):
        last = i == len(names) - 1
        connector = "└── " if last else "├── "
        child = node[name]
        if "__dir__" in child:
            suffix = " [空目录]" if child.get("__empty__") else ""
            lines.append(prefix + connector + name + "/" + suffix)
            _render_tree_node(child, prefix + ("    " if last else "│   "), lines)
        else:
            lines.append(prefix + connector + name)


def write_directory_tree(output_dir, input_root, files, dirs):
    path = os.path.join(output_dir, ARTIFACTS["tree"])
    trie = {}

    def ensure(parts):
        node = trie
        for p in parts:
            node = node.setdefault(p, {})
        return node

    for d in sorted(dirs, key=lambda x: x["rel_path"]):
        parts = d["rel_path"].split("/") if d["rel_path"] not in (".", "") else []
        node = ensure(parts)
        node["__dir__"] = True
        node["__empty__"] = d.get("empty", False)
    for f in sorted(files, key=lambda x: x["rel_path"]):
        parts = f["rel_path"].split("/")
        node = ensure(parts)
        node["__kind__"] = f.get("kind", "file")

    root_name = os.path.basename(os.path.abspath(input_root))
    lines = [root_name + "/"]
    _render_tree_node(trie, "", lines)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return ARTIFACTS["tree"]


def write_report_md(output_dir, input_root, summary, recon, errors, degradations):
    path = os.path.join(output_dir, ARTIFACTS["report"])
    lines = []
    lines.append("# 科研资料库交接报告")
    lines.append("")
    lines.append("- 输入根目录：`%s`" % input_root)
    lines.append("- 状态摘要：%s" % json.dumps(summary, ensure_ascii=False))
    lines.append("")
    lines.append("## 恢复对账")
    lines.append("")
    lines.append("- matches: %d" % recon["counts"]["matches"])
    lines.append("- mismatches: %d" % recon["counts"]["mismatches"])
    lines.append("- missing: %d" % recon["counts"]["missing"])
    lines.append("- orphaned: %d" % recon["counts"]["orphaned"])
    lines.append("")
    if errors:
        lines.append("## 错误（未静默跳过）")
        lines.append("")
        for e in errors:
            lines.append("- `%s`: %s (%s)" % (e.get("item"), e.get("error"), e.get("detail", "")))
        lines.append("")
    if degradations:
        lines.append("## 降级")
        lines.append("")
        for d in degradations:
            lines.append("- `%s`: %s (%s)" % (d.get("item"), d.get("error"), d.get("detail", "")))
        lines.append("")
    lines.append("## 说明")
    lines.append("")
    lines.append("本报告由确定性脚本生成；重复组按内容 SHA-256 判定，版本链为启发式候选，须经人工复核确认。")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return ARTIFACTS["report"]


# --------------------------------------------------------------------------- #
# result.json assembly
# --------------------------------------------------------------------------- #


def load_case(case_json_path, task_path=None):
    info = {
        "case_id": None,
        "contract": None,
        "contract_key": None,
        "errors": [],
        "task_text": "",
        "case_json_present": False,
    }
    if task_path and os.path.isfile(task_path):
        info["task_text"] = read_text_any(task_path) or ""
    if not os.path.isfile(case_json_path):
        info["errors"].append(
            {"item": case_json_path, "error": "case_json_missing", "detail": "no case.json found"}
        )
        return info
    info["case_json_present"] = True
    data, err = load_json_file(case_json_path)
    if data is None:
        info["errors"].append(
            {"item": case_json_path, "error": err, "detail": "case.json could not be parsed"}
        )
        return info
    if not isinstance(data, dict):
        info["errors"].append(
            {"item": case_json_path, "error": "case_json_not_object", "detail": "top level must be an object"}
        )
        return info
    info["case_id"] = pick(
        data, ("case_id", "caseId", "id", "challenge_id", "challengeId", "armiId")
    ) or os.path.splitext(os.path.basename(case_json_path))[0]
    for k in CONTRACT_KEYS:
        if k in data and isinstance(data[k], (dict, list)):
            info["contract"] = data[k]
            info["contract_key"] = k
            break
    if info["contract"] is None:
        # contract may be nested under an "output"/"result" object.
        nested = data.get("output") or data.get("result") or {}
        if isinstance(nested, dict):
            for k in CONTRACT_KEYS:
                if k in nested and isinstance(nested[k], (dict, list)):
                    info["contract"] = nested[k]
                    info["contract_key"] = k
                    break
    return info


def contract_placeholders(contract):
    out = {}
    if isinstance(contract, dict):
        for k, v in contract.items():
            if isinstance(v, list):
                out[k] = []
            elif isinstance(v, dict):
                out[k] = {}
            else:
                out[k] = None
    elif isinstance(contract, list):
        for k in contract:
            out[str(k)] = None
    return out


def merge_contract(result, placeholders):
    """Insert contract keys at top level; on reserved-key collision, move to computed."""
    moved = {}
    for k, v in placeholders.items():
        if k in RESERVED_KEYS:
            moved["_contract_%s" % k] = v
        else:
            result[k] = v
    if moved:
        result.setdefault("computed", {})
        result["computed"].update(moved)


# Conservative auto-fill: map contract keys to mechanical data when the name is
# an exact/simple match.  Anything left null is listed in uncertainty and must
# be completed by the Agent through an incremental --update patch.
CONTRACT_AUTOFILL = {
    "caseid": "meta.case_id",
    "status": "meta.status",
    "files": "computed.files",
    "filelist": "computed.files",
    "manifest": "computed.files",
    "inventory": "computed.files",
    "summary": "computed.summary",
    "duplicates": "computed.duplicate_groups",
    "duplicategroups": "computed.duplicate_groups",
    "dedup": "computed.duplicate_groups",
    "versions": "computed.version_candidates",
    "versioncandidates": "computed.version_candidates",
    "versionchain": "computed.version_candidates",
    "versionnotes": "computed.version_notes",
    "aliases": "computed.aliases",
    "reconciliation": "computed.reconciliation",
    "recovery": "computed.reconciliation",
    "index": "computed.index",
    "answers": "computed.answers",
    "directorytree": "artifact.tree",
    "manifestpath": "artifact.manifest",
    "recoveryreport": "artifact.recovery",
    "dedupversion": "artifact.dedup_version",
    "indexfile": "artifact.index",
    "operationlog": "artifact.operation_log",
    "report": "artifact.report",
}


def _resolve_computed(computed, dotted):
    cur = computed
    parts = dotted.split(".")
    if parts and parts[0] == "computed":
        parts = parts[1:]
    for part in parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def auto_fill_contract(result, computed, artifacts):
    """Fill contract placeholders whose names map to mechanical data; return unfilled list."""
    unfilled = []
    for k, v in list(result.items()):
        if k in RESERVED_KEYS:
            continue
        nk = norm_key(k)
        target = CONTRACT_AUTOFILL.get(nk)
        if target is None:
            if v in (None, [], {}):
                unfilled.append(k)
            continue
        if target.startswith("meta."):
            val = result.get(target.split(".", 1)[1])
        elif target.startswith("artifact."):
            name = target.split(".", 1)[1]
            val = artifacts.get(name)
        else:
            val = _resolve_computed(computed, target)
        if val is not None and val not in ([], {}):
            result[k] = val
        elif v in (None, [], {}):
            unfilled.append(k)
    return unfilled


def build_sources(files, dup_groups, version_candidates, aliases, recon, version_notes):
    sources = []
    for g in dup_groups[:50]:
        sources.append(
            {
                "item": "duplicate_group",
                "locator": " / ".join(g["paths"]),
                "quote": "SHA-256=%s size=%s" % (g["sha256"], g["size"]),
                "type": "content_hash",
            }
        )
    for vc in version_candidates[:50]:
        sources.append(
            {
                "item": "version_candidate",
                "locator": " / ".join(c["rel_path"] for c in vc["chain"]),
                "quote": "stem=%r extension=%s distinct_hashes=%d" % (
                    vc["stem"], vc["extension"], vc["distinct_hashes"],
                ),
                "type": "heuristic",
            }
        )
    for n in version_notes[:50]:
        sources.append(
            {
                "item": "version_note",
                "locator": n["locator"],
                "quote": n["quote"],
                "type": "task_markdown",
            }
        )
    for m in recon["mismatches"][:50]:
        sources.append(
            {
                "item": "reconcile_mismatch",
                "locator": m["locator"],
                "quote": "catalog_hash=%s actual_hash=%s path=%s" % (
                    m.get("catalog_hash"), m.get("actual_hash"), m.get("catalog_path"),
                ),
                "type": "source_conflict",
            }
        )
    for hg in aliases["hardlink_groups"][:20]:
        sources.append(
            {
                "item": "hardlink_group",
                "locator": " / ".join(hg["paths"]),
                "quote": "inode=%s nlink=%s" % (hg["inode"], hg["nlink"]),
                "type": "inode_alias",
            }
        )
    return sources


def build_result(input_root, case, files, dirs, errors, degradations, computed,
                 artifacts, status, runtime, contract_placeholders_dict=None):
    result = OrderedDict()
    result["case_id"] = case.get("case_id")
    result["status"] = status
    if contract_placeholders_dict is not None:
        merge_contract(result, contract_placeholders_dict)
    result["output_dir"] = "/app/output"
    result["artifacts"] = artifacts
    result["computed"] = computed

    unfilled = auto_fill_contract(result, computed, artifacts) if contract_placeholders_dict else []

    recon = computed.get("reconciliation", {})
    aliases = computed.get("aliases", {})
    version_notes = computed.get("version_notes", [])
    dup_groups = computed.get("duplicate_groups", [])
    version_candidates = computed.get("version_candidates", [])

    result["sources"] = build_sources(files, dup_groups, version_candidates, aliases, recon, version_notes)
    uncertainty = [
        {
            "item": "version_chain",
            "reason": "版本链为启发式候选：按归一化主干 + 内容哈希/相似度 + mtime 排序，须人工确认谁替代谁",
            "severity": "medium",
        },
        {
            "item": "mime_hint",
            "reason": "文件类型由扩展名 + 魔数头部推断，未做完整内容解析",
            "severity": "low",
        },
        {
            "item": "catalog_path_matching",
            "reason": "库目录条目与文件按 path/相对路径（必要时回退到 basename）匹配，歧义时可能需人工裁决",
            "severity": "medium",
        },
    ]
    for k in unfilled:
        uncertainty.append(
            {
                "item": "contract_field_unfilled",
                "field": k,
                "reason": "output_contract 字段在机械层未找到对应值，需语义层补充",
                "severity": "medium",
            }
        )
    result["uncertainty"] = uncertainty
    result["pending_human"] = [
        {
            "item": "duplicate_retention",
            "detail": "每个重复组保留哪个副本、其余去重或归档，需人工按题目规则决定",
        },
        {
            "item": "version_chain_confirmation",
            "detail": "版本候选链的先后与替代关系需人工复核确认",
        },
        {
            "item": "catalog_path_ambiguity",
            "detail": "库目录条目仅能按 basename 唯一匹配的文件需人工确认",
        },
    ]
    result["change_log"] = [
        {
            "time": runtime["started_at"],
            "actor": "scripts/core_impl.py",
            "step": "render",
            "detail": "deterministic pipeline result written",
        }
    ]
    result["errors"] = errors
    result["degradations"] = degradations
    result["runtime"] = runtime
    result["limitations"] = [
        "本 Skill 只做机械确定层（盘点/去重/版本候选/别名/对账/索引/问答），语义归位由 Agent 增量补充",
        "大于 %d 字节的文件不做内容哈希，仅记录路径、大小、权限与类型" % DEFAULT_HASH_LIMIT,
        "不移动、不覆盖、不删除源文件；所有结论均可由他人按记录复跑核对",
    ]
    return result


def write_operation_log(output_dir, entries):
    path = os.path.join(output_dir, ARTIFACTS["operation_log"])
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n")
    return ARTIFACTS["operation_log"]


def ensure_dirs(*paths):
    for p in paths:
        if p:
            os.makedirs(p, exist_ok=True)


# --------------------------------------------------------------------------- #
# Pipeline driver
# --------------------------------------------------------------------------- #


def run_skeleton(case_json_path, task_path, output_dir, work_dir):
    """Phase 0: write a legal partial result.json skeleton immediately."""
    started = now_iso()
    ensure_dirs(output_dir, work_dir)
    case = load_case(case_json_path, task_path)
    status = "input_error" if case["errors"] else "partial"
    placeholders = contract_placeholders(case["contract"]) if case.get("contract") is not None else None
    result = OrderedDict()
    result["case_id"] = case.get("case_id")
    result["status"] = status
    if placeholders is not None:
        merge_contract(result, placeholders)
    result["output_dir"] = "/app/output"
    result["artifacts"] = {}
    result["computed"] = {"summary": {}, "files": [], "dirs": []}
    result["sources"] = []
    result["uncertainty"] = [
        {
            "item": "skeleton",
            "reason": "骨架阶段尚未完成盘点与分析",
            "severity": "low",
        }
    ]
    result["pending_human"] = []
    result["change_log"] = [
        {
            "time": started,
            "actor": "scripts/core_impl.py",
            "step": "skeleton",
            "detail": "partial skeleton written before analysis",
        }
    ]
    result["errors"] = case["errors"]
    result["degradations"] = []
    result["runtime"] = {
        "started_at": started,
        "finished_at": now_iso(),
        "python_version": "%d.%d.%d" % sys.version_info[:3],
        "platform": sys.platform,
        "hash_limit_bytes": DEFAULT_HASH_LIMIT,
        "request_budget_hint": "64 model requests / 900 seconds",
    }
    result["limitations"] = ["partial skeleton: analysis not yet run"]
    result_path = os.path.join(output_dir, "result.json")
    write_json_atomic(result_path, result)
    return result_path


def run_pipeline(case_json_path, task_path, input_root, output_dir, work_dir,
                 hash_limit=None, want_artifacts=True):
    """Full deterministic pipeline: crawl -> analyze -> render. Never raises."""
    started = now_iso()
    started_t = time.time()
    ensure_dirs(output_dir, work_dir)
    if hash_limit is None:
        hash_limit = DEFAULT_HASH_LIMIT

    case = load_case(case_json_path, task_path)
    files, dirs, errors = crawl(input_root)
    # Case-level errors (missing case.json, bad JSON) must be visible in the
    # result, never silently dropped.
    errors = list(case.get("errors", [])) + errors
    degradations = []

    dup_groups = build_duplicate_groups(files)
    version_candidates = build_version_candidates(files)
    aliases = build_aliases(files)
    catalog_parses, cat_degradations = discover_catalogs(files, input_root)
    degradations.extend(cat_degradations)
    recon = reconcile(catalog_parses, files)
    version_notes = parse_version_notes(case.get("task_text"))
    index = build_index(files, dirs)
    answers = mechanical_answers(files, dirs, dup_groups, version_candidates, aliases, recon, index)
    summary = build_summary(files, dirs, dup_groups, version_candidates, aliases, recon, catalog_parses)

    computed = {
        "summary": summary,
        "files": files,
        "dirs": dirs,
        "duplicate_groups": dup_groups,
        "version_candidates": version_candidates,
        "version_notes": version_notes,
        "aliases": aliases,
        "catalog_parses": catalog_parses,
        "reconciliation": recon,
        "index": index,
        "answers": answers,
    }

    # IR-integrity gate: a usable IR must contain a file/dir list even if empty.
    if not isinstance(files, list) or not isinstance(dirs, list):
        errors.append(
            {"item": "ir", "error": "ir_integrity_failed", "detail": "crawl returned malformed data"}
        )

    status = "ok"
    if case["errors"]:
        status = "input_error"
    elif not files and not dirs:
        status = "insufficient_evidence"

    artifacts = OrderedDict((name, None) for name in ARTIFACTS)
    if want_artifacts:
        try:
            artifacts["manifest"] = write_manifest_csv(output_dir, files, dirs)
            artifacts["tree"] = write_directory_tree(output_dir, input_root, files, dirs)
            artifacts["dedup_version"] = write_json_atomic(
                os.path.join(output_dir, ARTIFACTS["dedup_version"]),
                {
                    "duplicate_groups": dup_groups,
                    "version_candidates": version_candidates,
                    "version_notes": version_notes,
                    "aliases": aliases,
                },
            ) or ARTIFACTS["dedup_version"]
            artifacts["recovery"] = write_json_atomic(
                os.path.join(output_dir, ARTIFACTS["recovery"]),
                {"reconciliation": recon, "errors": errors, "degradations": degradations},
            ) or ARTIFACTS["recovery"]
            artifacts["index"] = write_json_atomic(
                os.path.join(output_dir, ARTIFACTS["index"]),
                {
                    "index": index,
                    "answers": answers,
                    "search_example": search_index(index, "version")[:20],
                },
            ) or ARTIFACTS["index"]
            artifacts["report"] = write_report_md(output_dir, input_root, summary, recon, errors, degradations)
        except OSError as e:
            errors.append({"item": "render", "error": "write_failed", "detail": str(e)})
            status = "partial" if status == "ok" else status

    log_entries = [
        {
            "time": started,
            "actor": "scripts/core_impl.py",
            "step": "crawl",
            "detail": "%d files, %d dirs, %d errors" % (len(files), len(dirs), len(errors)),
        },
        {
            "time": started,
            "actor": "scripts/core_impl.py",
            "step": "analyze",
            "detail": "%d dup groups, %d version candidates, %d catalogs" % (
                len(dup_groups), len(version_candidates), len(catalog_parses),
            ),
        },
        {
            "time": started,
            "actor": "scripts/core_impl.py",
            "step": "render",
            "detail": "artifacts written",
        },
    ]
    artifacts["operation_log"] = write_operation_log(output_dir, log_entries)

    runtime = {
        "started_at": started,
        "finished_at": now_iso(),
        "duration_seconds": round(time.time() - started_t, 3),
        "python_version": "%d.%d.%d" % sys.version_info[:3],
        "platform": sys.platform,
        "hash_limit_bytes": hash_limit,
        "request_budget_hint": "64 model requests / 900 seconds",
    }

    placeholders = contract_placeholders(case["contract"]) if case.get("contract") is not None else None
    result = build_result(
        input_root, case, files, dirs, errors, degradations, computed,
        artifacts, status, runtime, placeholders,
    )
    result_path = os.path.join(output_dir, "result.json")
    write_json_atomic(result_path, result)
    return result_path


# --------------------------------------------------------------------------- #
# Incremental update & check
# --------------------------------------------------------------------------- #


def deep_merge(base, patch):
    """Recursively merge patch into base; lists and scalars are replaced."""
    if isinstance(base, dict) and isinstance(patch, dict):
        for k, v in patch.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                deep_merge(base[k], v)
            else:
                base[k] = v
        return base
    return patch


def apply_patch(result_path, patch_path):
    """Deep-merge a patch file into result.json (incremental improvement)."""
    patch, err = load_json_file(patch_path)
    if patch is None:
        return False, "patch %s: %s" % (patch_path, err)
    result, err = load_json_file(result_path)
    if result is None:
        return False, "result %s: %s" % (result_path, err)
    before = safe_json_dumps(result)
    merged = deep_merge(result, patch)
    if merged.get("change_log") is not None:
        merged.setdefault("change_log", [])
        merged["change_log"].append(
            {
                "time": now_iso(),
                "actor": "patch",
                "step": "update",
                "detail": "merged %s" % os.path.basename(patch_path),
            }
        )
    write_json_atomic(result_path, merged)
    changed = before != safe_json_dumps(merged)
    return True, ("changed" if changed else "no_change")


def check_result(result_path, case_json_path):
    """Validate result.json against case.json contract. Returns (ok, issues)."""
    issues = []
    result, err = load_json_file(result_path)
    if result is None:
        return False, ["result.json %s" % err]
    if not isinstance(result, dict):
        return False, ["result.json top level is not an object"]
    if not result.get("case_id"):
        issues.append("case_id is empty")
    if result.get("status") not in STATUS_VALUES:
        issues.append("status invalid: %r" % result.get("status"))
    for k in ("sources", "uncertainty", "change_log"):
        if k not in result:
            issues.append("missing key: %s" % k)

    case = load_case(case_json_path)
    if case.get("contract") is not None:
        for k in contract_placeholders(case["contract"]).keys():
            if k not in result:
                issues.append("missing contract field: %s" % k)
    return (not issues), issues
