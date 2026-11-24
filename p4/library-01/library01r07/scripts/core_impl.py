#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — document library repair engine (standard library only).

Deterministic inventory / dedup / version / alias / reconciliation / index
pipeline for a research document library.  Design goal (robustness variant):
empty input, malformed JSON, missing arguments, bad encodings, oversized
files, symlinks (including broken ones) and unreadable files must never crash
the run.  Every one of those conditions is recorded in ``errors`` or
``degradations`` and the skeleton ``result.json`` is always written first so
that a mid-run failure never leaves the output directory empty.

This module is invoked through ``main.py``.  It is intentionally free of
third-party dependencies and uses only the Python standard library.
"""

import argparse
import csv
import datetime
import difflib
import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import sys
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

# Files larger than this are not content-hashed (streaming hash is skipped).
# Path, size, mtime and permissions are still retained and the skip is
# recorded explicitly instead of being silently ignored.
MAX_HASH_BYTES = 256 * 1024 * 1024          # 256 MiB
# Decoded text beyond this is not read for catalog / metadata parsing.
MAX_TEXT_READ_BYTES = 4 * 1024 * 1024       # 4 MiB
# Content used for text-diff based version comparison is truncated here.
MAX_DIFF_TEXT_BYTES = 256 * 1024            # 256 KiB

VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "change_log",
    "errors", "degradations", "runtime", "limitations", "pending_human",
    "computed", "artifacts",
}

CONTRACT_KEYS = ("output_contract", "output", "result_schema", "contract",
                 "outputs", "result_contract")

TEXT_EXTENSIONS = {
    ".md", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml", ".bib", ".ris",
    ".tex", ".py", ".r", ".m", ".c", ".cpp", ".h", ".java", ".js", ".ts",
    ".xml", ".html", ".htm", ".log", ".ini", ".cfg", ".conf", ".toml",
}

CATALOG_EXTS = {
    ".json": "json",
    ".csv": "csv",
    ".bib": "bibtex",
    ".ris": "ris",
    ".db": "sqlite",
    ".sqlite": "sqlite",
    ".sqlite3": "sqlite",
}

VERSION_NOISE = re.compile(
    r"[-_ ()\[\]]*"
    r"(?:v|ver|version)?\s*[-_.]?\d+(?:\.\d+)*(?:[a-z]\d*)?"
    r"|[-_ ()\[\]]*(?:final|draft|old|new|copy|backup|bak|rev|edited|clean|"
    r"merged|orig|original|latest|updated|revision|r\d+)(?:\s*\d*)?",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def now_iso():
    """UTC timestamp in a stable ISO-8601 second format."""
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def mtime_iso(ts):
    try:
        return datetime.datetime.fromtimestamp(
            ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, ValueError, OverflowError):
        return None


def _json_default(obj):
    if isinstance(obj, (set, tuple)):
        return sorted(list(obj))
    return str(obj)


def dump_json(path, data, ctx=None):
    """Write JSON with a trailing newline; never raises (reports to ctx)."""
    tmp = path + ".tmp"
    try:
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2,
                      default=_json_default)
            fh.write("\n")
        os.replace(tmp, path)
        return True
    except (OSError, IOError, TypeError, ValueError) as exc:
        if ctx is not None:
            ctx.add_error("write_json " + path, str(exc))
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def load_json(path):
    """Return (data, error).  Decoding never crashes; malformed JSON is
    reported through the error string instead."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except (OSError, IOError) as exc:
        return None, "read_error: %s" % exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # Last-resort lossy decode keeps the process alive.
        text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as exc:
        return None, "json_error: %s" % exc


def read_text_limited(path, limit=MAX_TEXT_READ_BYTES):
    """Read a UTF-8 (or UTF-8-sig) text file, capped and with a bad-encoding
    fallback.  Returns (text, error)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(limit + 1)
        truncated = len(raw) > limit
        raw = raw[:limit]
    except (OSError, IOError) as exc:
        return "", "read_error: %s" % exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            text = raw.decode(enc)
            if truncated:
                text += "\n[truncated]"
            return text, None
        except UnicodeDecodeError:
            continue
    text = raw.decode("utf-8", errors="replace")
    if truncated:
        text += "\n[truncated]"
    return text, "bad_encoding: lossy utf-8 replacement used"


def sha256_file(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def normalize_path(p):
    if p is None:
        return ""
    p = str(p).strip().replace("\\", "/")
    p = p.strip("\"'")
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    # Collapse duplicate slashes and '.' segments.
    p = re.sub(r"/+", "/", p)
    return p


def tokenize(text):
    if not text:
        return []
    text = text.lower()
    toks = re.findall(r"[0-9a-z_]+|[\u4e00-\u9fff]+", text)
    out = []
    for t in toks:
        if t.isdigit():
            out.append(t)
        elif len(t) >= 2:
            out.append(t)
    return out


# ---------------------------------------------------------------------------
# Run context: honest bookkeeping
# ---------------------------------------------------------------------------

class RunContext:
    def __init__(self, input_root, output_dir, work_dir):
        self.input_root = input_root
        self.output_dir = output_dir
        self.work_dir = work_dir
        self.start_time = time.time()
        self.change_log = []
        self.errors = []
        self.degradations = []
        self.op_log = []

    def add_error(self, item, detail):
        rec = {"item": item, "detail": detail, "time": now_iso()}
        self.errors.append(rec)
        self.op_log.append({"time": rec["time"], "actor": "skill",
                            "step": "error", "detail": rec})

    def add_degradation(self, item, detail):
        rec = {"item": item, "detail": detail, "time": now_iso()}
        self.degradations.append(rec)
        self.op_log.append({"time": rec["time"], "actor": "skill",
                            "step": "degradation", "detail": rec})

    def log_step(self, step, detail):
        rec = {"time": now_iso(), "actor": "skill", "step": step,
               "detail": detail}
        self.change_log.append(rec)
        self.op_log.append(rec)

    def runtime(self):
        return {
            "started_at": mtime_iso(self.start_time),
            "duration_seconds": round(time.time() - self.start_time, 3),
            "python": sys.version.split()[0],
            "platform": sys.platform,
        }


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------

def find_contract(case):
    """Recognize the output contract under several common key names."""
    if not isinstance(case, dict):
        return {}
    for key in CONTRACT_KEYS:
        val = case.get(key)
        if isinstance(val, dict):
            return val
        if isinstance(val, list) and all(isinstance(x, str) for x in val):
            return {x: None for x in val}
    return {}


def find_case_id(case, task_text):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id", "challenge_id", "armiId"):
            val = case.get(key)
            if val:
                return str(val)
    if task_text:
        m = re.search(r"challenge_id[:：]\s*`?([A-Za-z0-9_\-]+)`?", task_text)
        if m:
            return m.group(1)
        m = re.search(r"(?:case[_ ]?id|caseId)[:：]\s*`?([^\s`]+)`?",
                      task_text, re.I)
        if m:
            return m.group(1)
    return "unknown-case"


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def stat_entry(full, rel, ctx):
    entry = {"path": rel}
    try:
        lst = os.lstat(full)
    except (OSError, IOError) as exc:
        entry.update({"type": "unreadable", "error": "lstat: %s" % exc})
        ctx.add_error("lstat " + rel, str(exc))
        return entry
    entry["size"] = int(lst.st_size)
    entry["mtime"] = mtime_iso(lst.st_mtime)
    entry["mode"] = oct(stat.S_IMODE(lst.st_mode))
    entry["nlink"] = int(getattr(lst, "st_nlink", 0) or 0)
    entry["dev"] = getattr(lst, "st_dev", None)
    entry["ino"] = getattr(lst, "st_ino", None)
    entry["uid"] = getattr(lst, "st_uid", None)
    entry["gid"] = getattr(lst, "st_gid", None)

    if stat.S_ISLNK(lst.st_mode):
        entry["type"] = "symlink"
        try:
            entry["link_target"] = os.readlink(full)
        except (OSError, IOError) as exc:
            entry["link_target"] = None
            entry["link_error"] = "readlink: %s" % exc
        try:
            entry["broken"] = not os.path.exists(full)
        except (OSError, IOError):
            entry["broken"] = True
    elif stat.S_ISDIR(lst.st_mode):
        entry["type"] = "directory"
    elif stat.S_ISREG(lst.st_mode):
        entry["type"] = "file"
        if entry["size"] > MAX_HASH_BYTES:
            entry["sha256"] = None
            entry["too_large"] = True
            ctx.add_degradation("oversized file not content-hashed", rel)
        else:
            try:
                entry["sha256"] = sha256_file(full)
            except (OSError, IOError) as exc:
                entry["sha256"] = None
                entry["unreadable"] = True
                entry["error"] = "hash: %s" % exc
                ctx.add_error("hash " + rel, str(exc))
    else:
        entry["type"] = "non_regular"
        ctx.add_degradation("non-regular filesystem object", rel)
    return entry


def inventory(input_root, ctx):
    """Walk input_root deterministically.  Symlinked directories are recorded
    but not descended into (avoids cycles)."""
    files = []
    dirs = []
    if not os.path.isdir(input_root):
        ctx.add_error("input root missing", input_root)
        return files, dirs
    for dirpath, dirnames, filenames in os.walk(input_root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        rel_dir = os.path.relpath(dirpath, input_root)
        if rel_dir == ".":
            rel_dir = ""
        else:
            rel_dir = rel_dir.replace(os.sep, "/")
        dirs.append({"path": rel_dir, "abs": dirpath})
        # Record symlinked directories as entries without descending.
        for name in list(dirnames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                rel = os.path.relpath(full, input_root).replace(os.sep, "/")
                files.append(stat_entry(full, rel, ctx))
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, input_root).replace(os.sep, "/")
            files.append(stat_entry(full, rel, ctx))
    return files, dirs


# ---------------------------------------------------------------------------
# Dedup + version candidates
# ---------------------------------------------------------------------------

def build_duplicate_groups(files):
    by_hash = {}
    for f in files:
        if f.get("type") == "file" and f.get("sha256"):
            by_hash.setdefault(f["sha256"], []).append(f["path"])
    groups = []
    for digest, paths in by_hash.items():
        paths = sorted(set(paths))
        if len(paths) > 1:
            groups.append({"sha256": digest, "count": len(paths),
                           "paths": paths})
    groups.sort(key=lambda g: (-g["count"], g["sha256"]))
    return groups


def normalize_stem(name):
    base = os.path.splitext(os.path.basename(name))[0]
    s = base.lower()
    s = VERSION_NOISE.sub(" ", s)
    s = s.replace("(", " ").replace(")", " ").replace("[", " ").replace("]", " ")
    s = re.sub(r"[_\-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .-_")
    return s


def text_diff_ratio(path_a, path_b):
    """Bounded, best-effort text similarity; None when not comparable."""
    if os.path.splitext(path_a)[1].lower() not in TEXT_EXTENSIONS:
        return None
    try:
        sa = os.path.getsize(path_a)
        sb = os.path.getsize(path_b)
    except OSError:
        return None
    if sa > MAX_DIFF_TEXT_BYTES or sb > MAX_DIFF_TEXT_BYTES:
        return None
    a, ea = read_text_limited(path_a, MAX_DIFF_TEXT_BYTES)
    b, eb = read_text_limited(path_b, MAX_DIFF_TEXT_BYTES)
    if ea or eb:
        return None
    if not a and not b:
        return 1.0
    try:
        return round(difflib.SequenceMatcher(None, a, b).ratio(), 4)
    except Exception:
        return None


def build_version_candidates(files, input_root):
    """Heuristic grouping by normalized stem + extension, ordered by mtime.
    This is explicitly heuristic: content hash is the ground truth for exact
    duplicates, while text similarity and size are signals for versions."""
    grouped = {}
    for f in files:
        if f.get("type") != "file":
            continue
        ext = os.path.splitext(f["path"])[1].lower()
        stem = normalize_stem(f["path"])
        if not stem:
            continue
        grouped.setdefault((stem, ext), []).append(f)
    candidates = []
    for (stem, ext), members in grouped.items():
        if len(members) < 2:
            continue
        members_sorted = sorted(
            members, key=lambda m: (m.get("mtime") or "", m["path"]))
        chain = []
        for cur in members_sorted:
            prev = chain[-1] if chain else None
            rel = {}
            if prev is not None:
                if prev.get("sha256") and cur.get("sha256"):
                    rel["same_content"] = prev["sha256"] == cur["sha256"]
                size_prev = prev.get("size", 0) or 0
                size_cur = cur.get("size", 0) or 0
                rel["size_delta"] = size_cur - size_prev
                same = bool(prev.get("sha256") and cur.get("sha256")
                            and prev["sha256"] == cur["sha256"])
                if not same:
                    abs_prev = os.path.join(input_root,
                                            prev["path"].replace("/", os.sep))
                    abs_cur = os.path.join(input_root,
                                           cur["path"].replace("/", os.sep))
                    rel["text_similarity"] = text_diff_ratio(abs_prev,
                                                             abs_cur)
            chain.append({"path": cur["path"], "sha256": cur.get("sha256"),
                          "size": cur.get("size"), "mtime": cur.get("mtime"),
                          "relation_to_previous": rel})
        candidates.append({"stem": stem, "extension": ext,
                           "heuristic": True, "chain": chain})
    candidates.sort(key=lambda c: (c["stem"], c["extension"]))
    return candidates


# ---------------------------------------------------------------------------
# Aliases: symlinks (incl. broken) and hard-link groups
# ---------------------------------------------------------------------------

def build_aliases(files):
    symlinks = []
    broken = []
    for f in files:
        if f.get("type") == "symlink":
            symlinks.append({
                "path": f["path"], "link_target": f.get("link_target"),
                "broken": bool(f.get("broken")),
            })
            if f.get("broken"):
                broken.append(f["path"])
    hardlink_groups = []
    by_inode = {}
    for f in files:
        if f.get("type") in ("file", "symlink") and f.get("nlink", 1) > 1:
            key = (f.get("dev"), f.get("ino"))
            if key == (None, None):
                continue
            by_inode.setdefault(key, []).append(f["path"])
    for paths in by_inode.values():
        paths = sorted(set(paths))
        if len(paths) > 1:
            hardlink_groups.append(paths)
    hardlink_groups.sort()
    symlinks.sort(key=lambda s: s["path"])
    broken.sort()
    return {
        "symlinks": symlinks,
        "broken_symlinks": broken,
        "hardlink_groups": hardlink_groups,
    }


# ---------------------------------------------------------------------------
# Catalog parsing (JSON / CSV / BibTeX / RIS / SQLite)
# ---------------------------------------------------------------------------

PATH_FIELDS = ("path", "file", "filename", "file_path", "relative_path",
               "rel_path", "location", "uri", "filepath")
HASH_FIELDS = ("sha256", "sha_256", "hash", "md5", "checksum", "content_hash")


def _record_to_entries(record, source, ctx):
    """Convert one catalog record into zero or more expected entries."""
    if not isinstance(record, dict):
        return []
    entries = []
    path = None
    for k in PATH_FIELDS:
        if k in record and record[k]:
            path = normalize_path(record[k])
            break
    if path is None and isinstance(record.get("files"), list):
        for sub in record["files"]:
            p = normalize_path(sub if isinstance(sub, str)
                                else sub.get("path") if isinstance(sub, dict)
                                else None)
            if p:
                entries.append({"path": p, "hash": None, "source": source,
                                "extra": record})
        return entries
    if path is None:
        return []
    digest = None
    for k in HASH_FIELDS:
        if k in record and record[k]:
            digest = str(record[k]).lower()
            break
    entries.append({"path": path, "hash": digest, "source": source,
                    "extra": record})
    return entries


def parse_json_catalog(text, source, ctx):
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        ctx.add_degradation("catalog json parse", "%s: %s" % (source, exc))
        return []
    if isinstance(data, dict):
        for key in ("files", "items", "entries", "records", "documents"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            return []
    if not isinstance(data, list):
        return []
    out = []
    for rec in data:
        out.extend(_record_to_entries(rec, source, ctx))
    return out


def parse_csv_catalog(path, source, ctx):
    out = []
    try:
        with io.open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if row:
                    out.extend(_record_to_entries(row, source, ctx))
    except (OSError, IOError, csv.Error, UnicodeError) as exc:
        ctx.add_degradation("catalog csv parse", "%s: %s" % (source, exc))
    return out


def parse_bibtex(text, source, ctx):
    out = []
    # @type{key, field = {value}, ...}
    pattern = re.compile(
        r"@\w+\s*\{([^,]+),(.*?)\}\s*(?=@\w+\s*\{|\Z)", re.S)
    for m in pattern.finditer(text):
        key = m.group(1).strip()
        body = m.group(2)
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*[\{|\"]([^}\"]*)[\}\"]", body):
            fields[fm.group(1).lower()] = fm.group(2).strip()
        rec = {"bibkey": key}
        rec.update(fields)
        out.extend(_record_to_entries(rec, source, ctx))
    return out


def parse_ris(text, source, ctx):
    out = []
    rec = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if len(line) >= 6 and line[2:4] == "  ":
            tag, value = line[:2].upper(), line[6:].strip()
            if tag == "TY":
                rec = {"ris_type": value}
            elif tag == "ER":
                out.extend(_record_to_entries(rec, source, ctx))
                rec = {}
            else:
                tag_l = tag.lower()
                if tag_l in rec and not isinstance(rec[tag_l], list):
                    rec[tag_l] = [rec[tag_l]]
                if isinstance(rec.get(tag_l), list):
                    rec[tag_l].append(value)
                else:
                    rec[tag_l] = value
    return out


def parse_sqlite_catalog(path, source, ctx):
    out = []
    try:
        con = sqlite3.connect(path)
    except sqlite3.Error as exc:
        ctx.add_degradation("sqlite_open_error", "%s: %s" % (source, exc))
        return out
    try:
        cur = con.cursor()
        try:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cur.fetchall()]
        except sqlite3.Error as exc:
            ctx.add_degradation("sqlite_query_error",
                                "%s: %s" % (source, exc))
            tables = []
        for table in tables:
            try:
                cur.execute("PRAGMA table_info(%s)" % table)
                cols = [r[1] for r in cur.fetchall()]
            except sqlite3.Error:
                cols = []
            path_col = next((c for c in cols if c.lower() in PATH_FIELDS),
                            None)
            if path_col is None:
                continue
            try:
                cur.execute('SELECT * FROM "%s" LIMIT 2000' % table)
                for row in cur.fetchall():
                    rec = dict(zip(cols, row))
                    out.extend(_record_to_entries(rec, source, ctx))
            except sqlite3.Error as exc:
                ctx.add_degradation("sqlite_query_error",
                                    "%s:%s: %s" % (source, table, exc))
    finally:
        try:
            con.close()
        except sqlite3.Error:
            pass
    return out


def discover_catalogs(input_root, ctx):
    entries = []
    catalogs_seen = 0
    for dirpath, dirnames, filenames in os.walk(input_root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext not in CATALOG_EXTS:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, input_root).replace(os.sep, "/")
            kind = CATALOG_EXTS[ext]
            catalogs_seen += 1
            parsed = []
            if kind == "json":
                text, err = read_text_limited(full)
                if err:
                    ctx.add_degradation("catalog text read",
                                        "%s: %s" % (rel, err))
                else:
                    parsed = parse_json_catalog(text, rel, ctx)
            elif kind == "csv":
                parsed = parse_csv_catalog(full, rel, ctx)
            elif kind in ("bibtex", "ris"):
                text, err = read_text_limited(full)
                if err:
                    ctx.add_degradation("catalog text read",
                                        "%s: %s" % (rel, err))
                else:
                    parsed = (parse_bibtex(text, rel, ctx) if kind == "bibtex"
                              else parse_ris(text, rel, ctx))
            elif kind == "sqlite":
                parsed = parse_sqlite_catalog(full, rel, ctx)
            entries.extend(parsed)
    return entries, catalogs_seen


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def build_reconciliation(files, catalog_entries, catalogs_seen):
    actual = {}
    for f in files:
        if f.get("type") == "file":
            actual[f["path"]] = f.get("sha256")
    expected = {}
    for e in catalog_entries:
        p = e["path"]
        if p in expected:
            # Multiple catalog rows may reference the same path; keep both.
            expected[p] = expected[p] + [e]
        else:
            expected[p] = [e]

    matches = []
    mismatches = []
    missing = []
    for p, rows in sorted(expected.items()):
        if p in actual:
            ok = True
            for r in rows:
                if r.get("hash"):
                    if actual[p] and r["hash"].lower() == actual[p].lower():
                        matches.append({"path": p, "expected_hash": r["hash"],
                                        "actual_hash": actual[p],
                                        "source": r["source"], "ok": True})
                    elif actual[p] is None:
                        ok = False
                        mismatches.append({"path": p,
                                           "expected_hash": r["hash"],
                                           "actual_hash": None,
                                           "source": r["source"],
                                           "reason": "actual not hashed"})
                    else:
                        ok = False
                        mismatches.append({"path": p,
                                           "expected_hash": r["hash"],
                                           "actual_hash": actual[p],
                                           "source": r["source"],
                                           "reason": "hash mismatch"})
                else:
                    matches.append({"path": p, "expected_hash": None,
                                    "actual_hash": actual[p],
                                    "source": r["source"], "ok": True})
            if not ok:
                continue
        else:
            for r in rows:
                missing.append({"path": p, "expected_hash": r.get("hash"),
                                "source": r["source"]})

    orphaned = []
    if catalogs_seen > 0:
        for f in files:
            if f.get("type") == "file" and f["path"] not in expected:
                orphaned.append(f["path"])
    matches.sort(key=lambda x: x["path"])
    mismatches.sort(key=lambda x: x["path"])
    missing.sort(key=lambda x: x["path"])
    orphaned.sort()
    return {
        "catalogs_seen": catalogs_seen,
        "matches": matches,
        "mismatches": mismatches,
        "missing": missing,
        "orphaned": orphaned,
        "match_count": len(matches),
        "mismatch_count": len(mismatches),
        "missing_count": len(missing),
        "orphaned_count": len(orphaned),
    }


# ---------------------------------------------------------------------------
# Index + mechanical answers
# ---------------------------------------------------------------------------

def build_index(files, catalog_entries, reconciliation):
    index = {}
    for f in files:
        terms = set(tokenize(f["path"]))
        if f.get("sha256"):
            terms.add(f["sha256"][:16])
        for t in terms:
            index.setdefault(t, []).append(f["path"])
    for e in catalog_entries:
        extra = e.get("extra") or {}
        for key in ("title", "author", "authors", "year", "doi", "bibkey",
                    "ris_type", "project", "instrument"):
            val = extra.get(key)
            if val:
                for t in tokenize(str(val)):
                    index.setdefault(t, []).append(e["path"])
    for term in index:
        index[term] = sorted(set(index[term]))
    return index


def build_answers(files, dirs, dup_groups, aliases, reconciliation,
                  version_candidates):
    file_entries = [f for f in files if f.get("type") == "file"]
    total_bytes = sum((f.get("size") or 0) for f in file_entries)
    ext_counts = {}
    for f in file_entries:
        ext = os.path.splitext(f["path"])[1].lower() or "(noext)"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
    too_large = [f["path"] for f in files if f.get("too_large")]
    unreadable = [f["path"] for f in files if f.get("unreadable")]
    return {
        "file_count": len(file_entries),
        "directory_count": len([d for d in dirs if d["path"] != ""]),
        "total_bytes": total_bytes,
        "duplicate_group_count": len(dup_groups),
        "duplicate_excess_files": sum(g["count"] - 1 for g in dup_groups),
        "version_candidate_group_count": len(version_candidates),
        "symlink_count": len(aliases["symlinks"]),
        "broken_symlink_count": len(aliases["broken_symlinks"]),
        "hardlink_group_count": len(aliases["hardlink_groups"]),
        "catalog_match_count": reconciliation["match_count"],
        "catalog_mismatch_count": reconciliation["mismatch_count"],
        "catalog_missing_count": reconciliation["missing_count"],
        "catalog_orphaned_count": reconciliation["orphaned_count"],
        "too_large_count": len(too_large),
        "unreadable_count": len(unreadable),
        "extensions": dict(sorted(ext_counts.items(),
                                  key=lambda kv: (-kv[1], kv[0]))),
        "too_large_files": too_large,
        "unreadable_files": unreadable,
    }


# ---------------------------------------------------------------------------
# Editable artifacts
# ---------------------------------------------------------------------------

def build_directory_tree(input_root, files, dirs):
    children = {}
    for d in dirs:
        p = d["path"]
        if p == "":
            continue
        parent = os.path.dirname(p)
        children.setdefault(parent, []).append(("d", os.path.basename(p), p))
    for f in files:
        p = f["path"]
        parent = os.path.dirname(p)
        children.setdefault(parent, []).append(("f", os.path.basename(p), p))

    # A directory is empty when no file lives inside it (directly or below).
    non_empty = set()
    for f in files:
        p = os.path.dirname(f["path"])
        while p:
            non_empty.add(p)
            p = os.path.dirname(p)

    filemap = {f["path"]: f for f in files}
    root_name = os.path.basename(os.path.abspath(input_root)) or input_root
    lines = [root_name + "/"]

    def emit(parent, prefix):
        entries = sorted(children.get(parent, []),
                         key=lambda x: (x[0] != "d", x[1].lower()))
        for i, (kind, name, full) in enumerate(entries):
            last = i == len(entries) - 1
            conn = "└── " if last else "├── "
            child_prefix = "    " if last else "│   "
            if kind == "d":
                empty = full not in non_empty
                suffix = " [空目录]" if empty else ""
                lines.append(prefix + conn + name + "/" + suffix)
                emit(full, prefix + child_prefix)
            else:
                mark = ""
                fobj = filemap.get(full)
                if fobj:
                    if fobj.get("broken"):
                        mark = " [断链]"
                    elif fobj.get("type") == "symlink":
                        mark = " -> " + str(fobj.get("link_target"))
                    elif fobj.get("too_large"):
                        mark = " [超大, 未哈希]"
                    elif fobj.get("unreadable"):
                        mark = " [不可读]"
                lines.append(prefix + conn + name + mark)
    emit("", "")
    return "\n".join(lines) + "\n"


def write_manifest_csv(files, path, ctx):
    fieldnames = ["path", "type", "size", "mtime", "mode", "sha256",
                  "nlink", "link_target", "broken", "too_large", "unreadable",
                  "error"]
    try:
        with io.open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames,
                                    extrasaction="ignore")
            writer.writeheader()
            for f in sorted(files, key=lambda x: x["path"]):
                row = dict(f)
                row["path"] = row.get("path", "")
                row["type"] = row.get("type", "")
                row["size"] = row.get("size", "")
                row["mtime"] = row.get("mtime", "")
                row["mode"] = row.get("mode", "")
                row["sha256"] = row.get("sha256", "")
                row["nlink"] = row.get("nlink", "")
                row["link_target"] = row.get("link_target", "")
                row["broken"] = "true" if row.get("broken") else ""
                row["too_large"] = "true" if row.get("too_large") else ""
                row["unreadable"] = "true" if row.get("unreadable") else ""
                row["error"] = row.get("error", "")
                writer.writerow(row)
        return True
    except (OSError, IOError) as exc:
        ctx.add_error("write manifest csv", str(exc))
        return False


def write_operation_log(entries, path, ctx):
    try:
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            for e in entries:
                fh.write(json.dumps(e, ensure_ascii=False,
                                    default=_json_default) + "\n")
        return True
    except (OSError, IOError) as exc:
        ctx.add_error("write operation log", str(exc))
        return False


def write_handoff_md(files, dirs, answers, dup_groups, aliases,
                     reconciliation, version_candidates, path, ctx):
    lines = [
        "# 科研资料库交接报告（机械盘点）",
        "",
        "> 本文件由 `scripts/main.py` 生成，是机器可复核的盘点结论；语义归位、",
        "> 版本最终判定与检索问答由 Agent 在 `result.json` 的 `computed` 之上补全。",
        "",
        "## 概览",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        "| 文件数 | %d |" % answers["file_count"],
        "| 目录数 | %d |" % answers["directory_count"],
        "| 总字节数 | %d |" % answers["total_bytes"],
        "| 内容重复组 | %d |" % answers["duplicate_group_count"],
        "| 版本候选组 | %d |" % answers["version_candidate_group_count"],
        "| 断链符号链接 | %d |" % answers["broken_symlink_count"],
        "| 目录缺失条目 | %d |" % answers["catalog_missing_count"],
        "| 目录不匹配条目 | %d |" % answers["catalog_mismatch_count"],
        "| 孤儿文件 | %d |" % answers["catalog_orphaned_count"],
        "",
        "## 内容重复组（按 SHA-256）",
        "",
    ]
    if dup_groups:
        for g in dup_groups:
            lines.append("- `%s…` (%d 份)" % (g["sha256"][:12], g["count"]))
            for p in g["paths"]:
                lines.append("  - `%s`" % p)
    else:
        lines.append("- 无")
    lines += ["", "## 版本候选组（启发式）", ""]
    if version_candidates:
        for v in version_candidates:
            lines.append("- 主干 `%s%s`:" % (v["stem"], v["extension"]))
            for m in v["chain"]:
                lines.append("  - `%s` @ %s (size=%s)" % (
                    m["path"], m.get("mtime") or "?", m.get("size", "?")))
    else:
        lines.append("- 无")
    lines += ["", "## 断链 / 别名", ""]
    if aliases["broken_symlinks"]:
        for p in aliases["broken_symlinks"]:
            lines.append("- `%s`" % p)
    else:
        lines.append("- 无断链")
    try:
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
        return True
    except (OSError, IOError) as exc:
        ctx.add_error("write handoff markdown", str(exc))
        return False


# ---------------------------------------------------------------------------
# result.json assembly
# ---------------------------------------------------------------------------

def apply_contract_fields(result, contract, ctx):
    """Backfill every contract field verbatim.  Missing values become null/[].
    Collisions with reserved keys are moved to computed._contract_<field>."""
    for key in contract:
        val = contract[key]
        if val is None:
            fill = None
        elif isinstance(val, list):
            fill = []
        elif isinstance(val, dict):
            fill = {}
        else:
            fill = None
        if key in RESERVED_KEYS:
            result.setdefault("computed", {})
            result["computed"]["_contract_" + key] = fill
        else:
            result.setdefault(key, fill)


def build_skeleton(case, task_text, contract, ctx):
    case_id = find_case_id(case, task_text)
    result = {
        "case_id": case_id,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "change_log": list(ctx.change_log),
        "errors": list(ctx.errors),
        "degradations": list(ctx.degradations),
        "runtime": ctx.runtime(),
        "limitations": [],
        "pending_human": [],
        "computed": {"_skeleton": True},
    }
    apply_contract_fields(result, contract, ctx)
    return result


def assemble_result(case, task_text, contract, case_err, files, dirs,
                    dup_groups, version_candidates, aliases, version_notes,
                    reconciliation, index, answers, artifacts, ctx):
    result = build_skeleton(case, task_text, contract, ctx)
    result["computed"] = {
        "_skeleton": False,
        "summary": answers,
        "files": files,
        "directory_count": len([d for d in dirs if d["path"] != ""]),
        "duplicate_groups": dup_groups,
        "version_candidates": version_candidates,
        "version_notes": version_notes,
        "aliases": aliases,
        "reconciliation": reconciliation,
        "answers": answers,
        "index": index,
        "artifacts": artifacts,
    }
    # Mechanical sources: the most trustworthy quotes are computed hashes and
    # resolved paths (they are derived, not fabricated).
    sources = []
    for g in dup_groups:
        sources.append({
            "item": "duplicate_group",
            "locator": g["paths"][0],
            "quote": "sha256=%s count=%d" % (g["sha256"], g["count"]),
        })
    for p in aliases["broken_symlinks"]:
        sources.append({"item": "broken_symlink", "locator": p,
                        "quote": "broken symlink: " + p})
    result["sources"] = sources

    # Honest, deterministic status decision.
    # Material = any inventory entry other than root-level case.json/task.md.
    def is_input_metadata(f):
        return f.get("path") in ("case.json", "task.md")
    has_material = any(not is_input_metadata(f) for f in files)

    if not os.path.isdir(ctx.input_root):
        result["status"] = "input_error"
    elif not has_material:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append({
            "item": "empty_input",
            "reason": "no material files found under input root",
            "severity": "high",
        })
    elif case_err:
        result["status"] = "input_error"
    elif not contract:
        result["status"] = "partial"
        result["uncertainty"].append({
            "item": "output_contract",
            "reason": "case.json present but no output_contract recognized",
            "severity": "medium",
        })
    elif ctx.errors:
        result["status"] = "partial"
    else:
        result["status"] = "ok"

    result["change_log"] = list(ctx.change_log)
    result["errors"] = list(ctx.errors)
    result["degradations"] = list(ctx.degradations)
    result["runtime"] = ctx.runtime()
    return result


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="document library repair (deterministic, stdlib only)")
    p.add_argument("--input", "--input-root", dest="input_root",
                   default=None,
                   help="input directory (default: /input)")
    p.add_argument("--output", "--output-dir", dest="output_dir",
                   default=None,
                   help="output directory (default: /app/output)")
    p.add_argument("--work", "--work-dir", dest="work_dir", default=None,
                   help="work directory (default: /app/work)")
    p.add_argument("--case-json", dest="case_json", default=None,
                   help="path to case.json (default: <input>/case.json)")
    p.add_argument("--task-path", dest="task_path", default=None,
                   help="path to task.md (default: <input>/task.md)")
    p.add_argument("--skeleton-only", action="store_true",
                   help="write only the partial result skeleton")
    p.add_argument("--update", dest="update_path", default=None,
                   help="merge a patch JSON into an existing result")
    p.add_argument("--result", dest="result_path", default=None,
                   help="result.json path for --update/--check")
    p.add_argument("--check", action="store_true",
                   help="validate an existing result.json")
    return p


def resolve_paths(args):
    input_root = args.input_root or DEFAULT_INPUT
    output_dir = args.output_dir or DEFAULT_OUTPUT
    work_dir = args.work_dir or DEFAULT_WORK
    case_json = args.case_json or os.path.join(input_root, "case.json")
    task_path = args.task_path or os.path.join(input_root, "task.md")
    result_path = args.result_path or os.path.join(output_dir, "result.json")
    return input_root, output_dir, work_dir, case_json, task_path, result_path


# ---------------------------------------------------------------------------
# Incremental update + check
# ---------------------------------------------------------------------------

MERGE_EXTEND_KEYS = {"sources", "change_log", "uncertainty", "errors",
                     "degradations", "pending_human", "limitations"}


def deep_merge(base, patch):
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return patch
    out = dict(base)
    for k, v in patch.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        elif k in MERGE_EXTEND_KEYS and isinstance(out.get(k), list) \
                and isinstance(v, list):
            seen = {json.dumps(x, sort_keys=True, default=str)
                    for x in out[k]}
            for item in v:
                sig = json.dumps(item, sort_keys=True, default=str)
                if sig not in seen:
                    out[k].append(item)
                    seen.add(sig)
        else:
            out[k] = v
    return out


def update_result(update_path, result_path, ctx):
    patch, err = load_json(update_path)
    if err:
        print("update: cannot load patch: %s" % err, file=sys.stderr)
        return 1
    cur, err = load_json(result_path)
    if err:
        print("update: cannot load result: %s" % err, file=sys.stderr)
        return 1
    merged = deep_merge(cur if isinstance(cur, dict) else {}, patch)
    merged.setdefault("change_log", []).append({
        "time": now_iso(), "actor": "skill", "step": "update",
        "detail": "merged patch from " + update_path})
    dump_json(result_path, merged, ctx)
    print("update: merged -> %s" % result_path)
    return 0


def check_result(result_path, case_json, ctx):
    fails = []
    data, err = load_json(result_path)
    if err:
        print("FAIL: cannot load result.json: %s" % err)
        return 1
    if not isinstance(data, dict):
        print("FAIL: result.json is not an object")
        return 1
    if not data.get("case_id"):
        fails.append("case_id empty")
    if data.get("status") not in VALID_STATUS:
        fails.append("invalid status %r" % data.get("status"))
    case, cerr = load_json(case_json)
    if cerr:
        print("WARN: case.json unreadable (%s); contract check skipped" % cerr)
    elif isinstance(case, dict):
        contract = find_contract(case)
        missing_fields = [k for k in contract if k not in data]
        if missing_fields:
            fails.append("missing contract fields: %s"
                         % ", ".join(sorted(missing_fields)))
    artifacts = (data.get("computed") or {}).get("artifacts") or []
    for art in artifacts:
        p = art.get("path") if isinstance(art, dict) else str(art)
        if not p or not os.path.exists(p):
            fails.append("artifact missing: %s" % p)
    for item in fails:
        print("FAIL: %s" % item)
    if fails:
        print("check: %d problem(s)" % len(fails))
        return 1
    print("check: ok (case_id=%s status=%s)" %
          (data.get("case_id"), data.get("status")))
    return 0


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def ensure_dirs(output_dir, work_dir, ctx):
    for d in (output_dir, work_dir):
        try:
            os.makedirs(d, exist_ok=True)
        except (OSError, IOError) as exc:
            ctx.add_error("cannot create directory " + d, str(exc))
            return False
    return True


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    args = build_parser().parse_args(argv)
    input_root, output_dir, work_dir, case_json, task_path, result_path = \
        resolve_paths(args)
    ctx = RunContext(input_root, output_dir, work_dir)

    if args.check:
        return check_result(result_path, case_json, ctx)

    if not ensure_dirs(output_dir, work_dir, ctx):
        # Write a minimal, honest result if at all possible; exit 2.
        fallback = {"case_id": "unknown-case", "status": "input_error",
                    "errors": list(ctx.errors),
                    "change_log": list(ctx.change_log),
                    "runtime": ctx.runtime()}
        dump_json(result_path, fallback, ctx)
        print("write failure: skeleton retained; exit 2", file=sys.stderr)
        return 2

    if args.update_path:
        return update_result(args.update_path, result_path, ctx)

    # ---- Read inputs -----------------------------------------------------
    case, case_err = load_json(case_json)
    task_text, task_err = read_text_limited(task_path) \
        if os.path.exists(task_path) else ("", "missing task.md")
    contract = find_contract(case) if isinstance(case, dict) else {}

    if case_err:
        ctx.add_error("case.json", case_err)
    if task_err and "missing" not in task_err:
        ctx.add_degradation("task.md", task_err)

    ctx.log_step("read_inputs",
                 "case_json=%s task_path=%s contract_fields=%d" %
                 (case_json, task_path, len(contract)))

    # ---- Skeleton first: never leave /app/output empty -------------------
    skeleton = build_skeleton(case, task_text, contract, ctx)
    if not case and not task_text:
        skeleton["status"] = "input_error"
    dump_json(result_path, skeleton, ctx)
    write_operation_log(ctx.op_log, os.path.join(output_dir,
                                                 "operation_log.jsonl"), ctx)
    if args.skeleton_only:
        print("skeleton: wrote %s (status=%s)" %
              (result_path, skeleton["status"]))
        return 0

    # ---- Stage 1: inventory ----------------------------------------------
    files, dirs = inventory(input_root, ctx)
    ctx.log_step("inventory", "%d files/objects, %d dirs" %
                 (len(files), len(dirs)))

    # ---- Stage 2: analyses -----------------------------------------------
    dup_groups = build_duplicate_groups(files)
    version_candidates = build_version_candidates(files, input_root)
    aliases = build_aliases(files)
    catalog_entries, catalogs_seen = discover_catalogs(input_root, ctx)
    reconciliation = build_reconciliation(files, catalog_entries,
                                          catalogs_seen)
    index = build_index(files, catalog_entries, reconciliation)
    answers = build_answers(files, dirs, dup_groups, aliases,
                            reconciliation, version_candidates)
    version_notes = extract_version_notes(task_text, case, ctx)

    ctx.log_step("analyze",
                 "dup_groups=%d version_candidates=%d aliases=%d "
                 "catalog_entries=%d" %
                 (len(dup_groups), len(version_candidates),
                  len(aliases["symlinks"]), len(catalog_entries)))

    # ---- Stage 3: artifacts ----------------------------------------------
    artifacts = []
    manifest_path = os.path.join(output_dir, "library_manifest.csv")
    if write_manifest_csv(files, manifest_path, ctx):
        artifacts.append({"path": manifest_path,
                          "name": "library_manifest.csv",
                          "kind": "csv"})
    tree_path = os.path.join(output_dir, "directory_tree.txt")
    try:
        with io.open(tree_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(build_directory_tree(input_root, files, dirs))
        artifacts.append({"path": tree_path,
                          "name": "directory_tree.txt", "kind": "txt"})
    except (OSError, IOError) as exc:
        ctx.add_error("write directory tree", str(exc))

    dedup_path = os.path.join(output_dir, "dedup_version.json")
    if dump_json(dedup_path, {
        "duplicate_groups": dup_groups,
        "version_candidates": version_candidates,
        "version_notes": version_notes,
        "aliases": aliases,
    }, ctx):
        artifacts.append({"path": dedup_path,
                          "name": "dedup_version.json", "kind": "json"})

    rec_path = os.path.join(output_dir, "recovery_report.json")
    if dump_json(rec_path, reconciliation, ctx):
        artifacts.append({"path": rec_path,
                          "name": "recovery_report.json", "kind": "json"})

    index_path = os.path.join(output_dir, "library_index.json")
    if dump_json(index_path, index, ctx):
        artifacts.append({"path": index_path,
                          "name": "library_index.json", "kind": "json"})

    handoff_path = os.path.join(output_dir, "library_manifest.md")
    if write_handoff_md(files, dirs, answers, dup_groups, aliases,
                        reconciliation, version_candidates, handoff_path, ctx):
        artifacts.append({"path": handoff_path,
                          "name": "library_manifest.md", "kind": "md"})

    log_path = os.path.join(output_dir, "operation_log.jsonl")
    write_operation_log(ctx.op_log, log_path, ctx)
    artifacts.append({"path": log_path,
                      "name": "operation_log.jsonl", "kind": "jsonl"})

    ctx.log_step("artifacts", "%d artifacts written" % len(artifacts))

    # ---- Stage 4: final result -------------------------------------------
    result = assemble_result(case, task_text, contract, case_err, files, dirs,
                             dup_groups, version_candidates, aliases,
                             version_notes, reconciliation, index, answers,
                             artifacts, ctx)
    dump_json(result_path, result, ctx)
    write_operation_log(ctx.op_log, log_path, ctx)

    print("finalize: %d objects, %d dup groups, %d version candidates, "
          "%d broken symlinks, %d missing -> %s (status=%s)" %
          (len(files), len(dup_groups), len(version_candidates),
           len(aliases["broken_symlinks"]), reconciliation["missing_count"],
           result_path, result["status"]))
    return 0


def extract_version_notes(task_text, case, ctx):
    notes = []
    if task_text:
        for line in task_text.splitlines():
            if re.search(r"版本说明|版本|version\s*note|行政版本", line,
                         re.I):
                notes.append({"locator": "task.md", "quote": line.strip()})
    if isinstance(case, dict):
        for key, val in case.items():
            if re.search(r"version|版本", key, re.I) and isinstance(val, str):
                notes.append({"locator": "case.json:" + key,
                              "quote": val[:500]})
    # Deduplicate by quote.
    seen = set()
    out = []
    for n in notes:
        if n["quote"] not in seen:
            seen.add(n["quote"])
            out.append(n)
    return out[:100]


if __name__ == "__main__":
    sys.exit(main())
