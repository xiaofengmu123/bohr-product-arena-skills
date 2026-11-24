# -*- coding: utf-8 -*-
"""core_impl.py — deterministic, standard-library-only document-library repair pipeline.

What this module does (mechanical layer; the Agent supplies the semantic layer):

  * discover ``/input`` recursively without assuming fixed file names;
  * stream content SHA-256 and collect size / mtime / mode / device-inode /
    link target, never loading a whole file into memory;
  * parse library catalogs (JSON / CSV / BibTeX / RIS / SQLite) tolerantly;
  * deduplicate by content hash (not filename), propose version chains by
    normalized stem + mtime, detect symlink (incl. broken) and hard-link aliases;
  * parse administrative version notes from ``task.md`` (heuristic), reconcile
    catalog records against actual files, and rebuild a searchable index;
  * answer a fixed set of mechanical questions and emit editable artifacts;
  * write an honest ``result.json`` that echoes every ``output_contract`` field.

Hard guarantees:
  * source files are never moved, overwritten, or deleted;
  * repeated runs are deterministic (sorted traversal, no randomness);
  * every stage degrades gracefully: empty input, bad JSON, bad encoding,
    missing arguments, oversized files, unreadable files, broken links, and
    broken SQLite all produce a legal artifact instead of a crash.
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
import stat as stat_module
import sys
import time
import urllib.parse
from collections import defaultdict

HASH_CHUNK = 1024 * 1024
QUOTE_BYTES = 2048
TEXT_READ_LIMIT = 8 * 1024 * 1024
DEFAULT_MAX_HASH_BYTES = 1 << 30  # 1 GiB; larger files are recorded, not hashed

RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "runtime", "limitations", "computed",
}
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CONTRACT_KEYS = (
    "output_contract", "output", "result_schema", "contract",
    "output_schema", "response_format", "expected_output",
)
CATALOG_EXTENSIONS = {".json", ".csv", ".bib", ".bibtex", ".ris", ".sqlite", ".sqlite3", ".db"}

ARTIFACT_NAMES = (
    "library_manifest.csv", "directory_tree.txt", "dedup_version.json",
    "recovery_report.json", "library_index.json", "library_manifest.md",
    "operation_log.jsonl",
)

# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def safe(obj):
    """Return a JSON-safe str; undecodable bytes are replaced, surrogates removed."""
    if isinstance(obj, bytes):
        obj = os.fsdecode(obj)
    if isinstance(obj, str):
        return obj.encode("utf-8", "replace").decode("utf-8")
    return obj


def has_surrogate(s):
    if not isinstance(s, str):
        return False
    return any(0xD800 <= ord(c) <= 0xDFFF for c in s)


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def read_text_file(path, max_bytes=None):
    with open(path, "rb") as f:
        data = f.read() if max_bytes is None else f.read(max_bytes)
    return data.decode("utf-8-sig", "replace")


def sha256_file(path):
    """Stream SHA-256. Returns (hexdigest, bytes_read)."""
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def file_quote(abs_path, size):
    """A short verbatim excerpt used as evidence for a file's identity."""
    try:
        with open(abs_path, "rb") as f:
            head = f.read(QUOTE_BYTES)
        if not head:
            return ""
        try:
            txt = head.decode("utf-8")
            printable = sum(c.isprintable() or c in "\n\r\t" for c in txt)
            if printable / max(len(txt), 1) > 0.8:
                return safe(txt[:200])
        except UnicodeDecodeError:
            pass
        return head[:32].hex()
    except OSError:
        return None


def write_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def read_json(path):
    """Tolerant JSON read: strips a UTF-8 BOM if present."""
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def deep_merge(base, patch):
    for k, v in patch.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


# ---------------------------------------------------------------------------
# input discovery
# ---------------------------------------------------------------------------

def load_case(case_path):
    """Return (obj_or_None, error_or_None). Never raises."""
    try:
        with open(case_path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return None, "case_not_found: %s" % case_path
    except OSError as e:
        return None, "case_unreadable: %s" % e
    try:
        text = raw.decode("utf-8-sig", "replace")
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, "case_bad_json: %s" % e
    except Exception as e:
        return None, "case_parse_error: %s" % e


def discover_contract(obj):
    """Find the output contract under one of several tolerated key names."""
    if not isinstance(obj, dict):
        return None, None
    for k in CONTRACT_KEYS:
        v = obj.get(k)
        if isinstance(v, dict):
            return k, v
    for sub in ("task", "case", "spec", "requirements", "instructions"):
        s = obj.get(sub)
        if isinstance(s, dict):
            k, v = discover_contract(s)
            if k is not None:
                return sub + "." + k, v
    return None, None


def extract_case_id(case_obj, task_text):
    if isinstance(case_obj, dict):
        for k in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
            v = case_obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (int, float)):
                return str(v)
    m = re.search(r"challenge_id:\s*`?([A-Za-z0-9_-]+)`?", task_text or "")
    if m:
        return m.group(1)
    m = re.search(r"case[_ -]?id[:=]\s*[\"']?([A-Za-z0-9_-]+)", task_text or "", re.I)
    if m:
        return m.group(1)
    return "unknown"


def contract_placeholder(field_value):
    if isinstance(field_value, dict):
        if "default" in field_value:
            return field_value["default"]
        t = str(field_value.get("type", "")).lower()
        if t in ("array", "list") or "items" in field_value:
            return []
        if t in ("object", "dict", "map"):
            return {}
        if t in ("string", "str", "text", "number", "int", "integer", "float", "boolean", "bool"):
            return None
        return None
    if isinstance(field_value, list):
        return []
    return None


def build_contract_payload(contract):
    payload = {}
    for field in sorted(contract.keys()):
        payload[field] = contract_placeholder(contract.get(field))
    return payload


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------

def file_entry(root, rel, name, cfg, degradations):
    abs_path = os.path.join(root, name)
    entry = {
        "path": safe(rel),
        "type": "file",
        "size": None,
        "mtime_epoch": None,
        "mtime_iso": None,
        "mode": None,
        "sha256": None,
        "status": "ok",
        "st_dev": None,
        "st_ino": None,
        "st_nlink": None,
        "link_target": None,
        "role": None,
    }
    try:
        st = os.lstat(abs_path)
    except OSError as e:
        entry["status"] = "unreadable"
        entry["error"] = safe(str(e))
        return entry

    entry["mode"] = "0o" + format(stat_module.S_IMODE(st.st_mode), "03o")
    entry["st_dev"] = getattr(st, "st_dev", None)
    entry["st_ino"] = getattr(st, "st_ino", None)
    entry["st_nlink"] = getattr(st, "st_nlink", None)

    if stat_module.S_ISLNK(st.st_mode):
        entry["type"] = "symlink"
        try:
            entry["link_target"] = safe(os.readlink(abs_path))
        except OSError as e:
            entry["link_target"] = "<unreadable:%s>" % safe(str(e))
        entry["status"] = "broken_symlink" if not os.path.exists(abs_path) else "symlink"
        if entry["status"] == "broken_symlink":
            degradations.append("broken symlink: %s" % entry["path"])
        return entry

    if stat_module.S_ISDIR(st.st_mode):
        entry["type"] = "dir"
        return entry

    if not stat_module.S_ISREG(st.st_mode):
        entry["type"] = "other"
        entry["status"] = "non_regular"
        degradations.append("non-regular file skipped: %s" % entry["path"])
        return entry

    entry["size"] = st.st_size
    try:
        entry["mtime_epoch"] = st.st_mtime
        entry["mtime_iso"] = _dt.datetime.fromtimestamp(
            st.st_mtime, tz=_dt.timezone.utc
        ).isoformat()
    except (OSError, ValueError, OverflowError):
        pass

    if st.st_size > cfg["max_hash_bytes"]:
        entry["status"] = "too_large"
        degradations.append("oversized file, hash skipped: %s (%d bytes)" % (entry["path"], st.st_size))
        return entry

    try:
        h, n = sha256_file(abs_path)
        entry["sha256"] = h
        if n != st.st_size:
            entry["status"] = "changed_during_read"
            degradations.append("file changed during read: %s" % entry["path"])
    except OSError as e:
        entry["status"] = "unreadable"
        entry["error"] = safe(str(e))
        degradations.append("unreadable file: %s (%s)" % (entry["path"], e))
    except Exception as e:
        entry["status"] = "unreadable"
        entry["error"] = safe(str(e))
        degradations.append("hash failure: %s (%s)" % (entry["path"], e))
    return entry


def build_inventory(root, cfg):
    """Recursively scan ``root``; deterministic (sorted walk)."""
    entries = []
    dir_entries = []
    errors = []
    degradations = []
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return {
            "entries": [], "dirs": [], "errors": ["input_root_missing: %s" % root],
            "degradations": [], "root": root,
        }

    def onerror(e):
        errors.append(safe(str(e)))

    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
        dirnames.sort()
        filenames.sort()
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""
        # record the directory itself (except root)
        if rel_dir:
            d = {
                "path": safe(rel_dir.replace(os.sep, "/")),
                "type": "dir",
                "size": None, "mtime_epoch": None, "mtime_iso": None,
                "mode": None, "sha256": None, "status": "ok",
                "st_dev": None, "st_ino": None, "st_nlink": None,
                "link_target": None, "role": None,
            }
            try:
                st = os.lstat(dirpath)
                d["mode"] = "0o" + format(stat_module.S_IMODE(st.st_mode), "03o")
                d["mtime_epoch"] = st.st_mtime
                d["mtime_iso"] = _dt.datetime.fromtimestamp(
                    st.st_mtime, tz=_dt.timezone.utc
                ).isoformat()
            except OSError:
                pass
            dir_entries.append(d)

        for name in filenames:
            rel = os.path.join(rel_dir, name).replace(os.sep, "/")
            base = os.path.basename(rel)
            e = file_entry(dirpath, rel, name, cfg, degradations)
            if base in ("case.json", "task.md"):
                e["role"] = "task_meta"
            entries.append(e)

    entries.sort(key=lambda x: x["path"])
    dir_entries.sort(key=lambda x: x["path"])
    return {
        "entries": entries, "dirs": dir_entries, "errors": errors,
        "degradations": degradations, "root": root,
    }


# ---------------------------------------------------------------------------
# tree rendering
# ---------------------------------------------------------------------------

def render_tree(entries, dir_entries):
    tree = {}
    dirs_set = {d["path"] for d in dir_entries}

    def ensure(path):
        node = tree
        for part in path.split("/"):
            node = node.setdefault(part, {})
        return node

    for d in sorted(dirs_set):
        if d:
            ensure(d)
    for e in sorted(entries, key=lambda x: x["path"]):
        parts = e["path"].split("/")
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = e

    lines = []

    def rec(node, name, ancestors):
        indent = "".join("    " if last else "|   " for last in ancestors)
        if isinstance(node, dict) and "type" not in node:
            children = sorted(node.items())
            if not children:
                lines.append(indent + name + "/ [空目录]")
            else:
                lines.append(indent + name + "/")
            for i, (cn, cv) in enumerate(children):
                rec(cv, cn, ancestors + (i == len(children) - 1,))
        else:
            e = node
            tail = ""
            if e.get("type") == "symlink":
                tail = " -> " + str(e.get("link_target"))
                if e.get("status") == "broken_symlink":
                    tail += " [断链]"
            elif e.get("status") in ("too_large", "unreadable", "non_regular", "changed_during_read"):
                tail = " [%s]" % e.get("status")
            lines.append(indent + name + tail)

    for i, (n, c) in enumerate(sorted(tree.items())):
        rec(c, n, (i == len(tree) - 1,))
    if not lines:
        lines.append("[空目录]")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# dedup / version / alias
# ---------------------------------------------------------------------------

def group_duplicates(entries):
    groups = defaultdict(list)
    for e in entries:
        if e.get("type") == "file" and e.get("sha256"):
            groups[e["sha256"]].append(e["path"])
    return {k: sorted(v) for k, v in groups.items() if len(v) > 1}


_VERSION_TOKEN_PATTERNS = [
    re.compile(r'\b(?:v|ver|version|rev|r)\s*\d+(?:[.]\d+)*\b'),
    re.compile(r'\b\d{4}[- ]\d{2}[- ]\d{2}\b'),
    re.compile(r'\b\d{8}\b'),
    re.compile(r'\(\d+\)'),
    re.compile(r'\b(?:copy|final|draft|old|new|backup|副本|最终|终稿|初稿|草稿)\b', re.I),
]


def normalize_stem(path):
    base = os.path.basename(path)
    stem, ext = os.path.splitext(base)
    s = stem.lower()
    # Flatten separators to spaces so version/date/copy tokens detach cleanly
    # (e.g. "report_v1" -> "report v1" -> strip "v1" -> "report").
    s = re.sub(r'[\s._-]+', ' ', s)
    for pat in _VERSION_TOKEN_PATTERNS:
        s = pat.sub(' ', s)
    s = re.sub(r'\s+', '_', s).strip('_')
    return s, ext.lower()


def diff_summary(a, b):
    if a.get("sha256") == b.get("sha256"):
        return {"kind": "identical_content"}
    if a.get("size") is not None and b.get("size") is not None:
        if a.get("size") == b.get("size"):
            return {"kind": "modified_same_size", "size_delta": 0}
        return {"kind": "size_changed", "size_delta": b["size"] - a["size"]}
    return {"kind": "unknown"}


def group_versions(entries):
    by_stem = defaultdict(list)
    for e in entries:
        if e.get("type") == "file" and e.get("sha256"):
            stem, ext = normalize_stem(e["path"])
            by_stem[(stem, ext)].append(e)
    chains = {}
    for (stem, ext), members in by_stem.items():
        hashes = {m["sha256"] for m in members}
        if len(hashes) < 2:
            continue  # all identical content -> duplicate group, not a version
        members = sorted(members, key=lambda m: (m.get("mtime_epoch") or 0.0, m["path"]))
        chain = []
        for i, m in enumerate(members):
            node = {
                "path": m["path"],
                "sha256": m["sha256"][:16],
                "size": m.get("size"),
                "mtime_iso": m.get("mtime_iso"),
                "parent": os.path.dirname(m["path"]),
            }
            if i > 0:
                node["vs_previous"] = diff_summary(members[i - 1], m)
            chain.append(node)
        chains[stem + ext] = {
            "stem": stem, "ext": ext, "confidence": "heuristic", "members": chain,
        }
    return chains


def detect_aliases(entries):
    symlinks = [e["path"] for e in entries if e.get("type") == "symlink"]
    broken = [e["path"] for e in entries if e.get("status") == "broken_symlink"]
    hd = defaultdict(list)
    for e in entries:
        if e.get("type") == "file" and e.get("st_nlink") and e.get("st_nlink") > 1 \
                and e.get("st_ino") is not None:
            hd[(e.get("st_dev"), e.get("st_ino"))].append(e["path"])
    hard_groups = [sorted(v) for v in hd.values() if len(v) > 1]
    hard_groups.sort()
    return {
        "symlinks": sorted(symlinks),
        "broken_symlinks": sorted(broken),
        "hardlink_groups": hard_groups,
    }


def parse_version_notes(task_text):
    notes = []
    if not task_text:
        return notes
    for i, line in enumerate(task_text.splitlines()):
        s = line.strip()
        if not s:
            continue
        if re.search(r'(版本|version|替代|supersed|修订|revision|rev\.|v\d+\.\d+|\b修订\b)', s, re.I):
            notes.append({"raw": safe(s[:400]), "locator": "task.md:%d" % (i + 1)})
    return notes


def align_version_notes(notes, entries):
    """Light heuristic alignment of administrative version notes to file paths."""
    tokens = {}
    for e in entries:
        if e.get("type") != "file":
            continue
        base = os.path.basename(e["path"])
        stem = os.path.splitext(base)[0]
        for t in (base, stem):
            if t and len(t) >= 3:
                tokens.setdefault(t.lower(), e["path"])
    for note in notes:
        linked = []
        for token, path in tokens.items():
            if re.search(re.escape(token), note["raw"], re.I) and path not in linked:
                linked.append(path)
        note["linked_paths"] = sorted(linked)
        note["alignment"] = "linked" if linked else "orphan"
    return notes


# ---------------------------------------------------------------------------
# catalogs (metadata) parsing
# ---------------------------------------------------------------------------

ID_KEYS = ("path", "file", "filename", "filepath", "location", "relative_path",
           "relpath", "url", "id", "key", "entry_key")
HASH_KEYS = ("sha256", "sha", "hash", "checksum", "etag", "md5")
SIZE_KEYS = ("size", "bytes", "filesize", "file_size", "length")


def normalize_record(rec, locator):
    d = {"locator": locator, "raw": rec, "file_id": None, "hash": None, "size": None}
    for k in ID_KEYS:
        if k in rec and rec[k] not in (None, ""):
            d["file_id"] = safe(str(rec[k]))
            break
    for k in HASH_KEYS:
        if k in rec and rec[k] not in (None, ""):
            d["hash"] = safe(str(rec[k])).lower()
            break
    for k in SIZE_KEYS:
        v = rec.get(k)
        if isinstance(v, (int, float)):
            d["size"] = int(v)
            break
    return d


def parse_json_catalog(path):
    try:
        raw = read_text_file(path, max_bytes=TEXT_READ_LIMIT)
        obj = json.loads(raw)
    except Exception as e:
        return None, "json_catalog_error: %s" % e
    records = []
    if isinstance(obj, list):
        records = [r for r in obj if isinstance(r, dict)]
    elif isinstance(obj, dict):
        for k in ("records", "items", "entries", "files", "data", "library", "documents", "catalog"):
            v = obj.get(k)
            if isinstance(v, list):
                records = [r for r in v if isinstance(r, dict)]
                break
        if not records:
            records = [v for v in obj.values() if isinstance(v, dict)]
    return records, None


def parse_csv_catalog(path):
    try:
        raw = read_text_file(path, max_bytes=TEXT_READ_LIMIT)
        sample = raw[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except Exception:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(raw), dialect=dialect)
        records = []
        for row in reader:
            clean = {}
            for k, v in row.items():
                if k is not None:
                    clean[safe(k)] = safe(v) if v is not None else ""
            records.append(clean)
    except Exception as e:
        return None, "csv_catalog_error: %s" % e
    return records, None


def parse_bibtex_catalog(path):
    try:
        raw = read_text_file(path, max_bytes=TEXT_READ_LIMIT)
    except Exception as e:
        return None, "bibtex_catalog_error: %s" % e
    body = re.sub(r'%.*', '', raw)
    records = []
    for m in re.finditer(r'@\s*([A-Za-z]+)\s*\{\s*([^,}]*?)\s*,\s*(.*?)\}\s*[,}]', body, re.S):
        etype = m.group(1).lower()
        key = m.group(2).strip()
        fields = m.group(3)
        rec = {"entry_type": etype, "bib_key": key}
        for fm in re.finditer(r'([A-Za-z][A-Za-z0-9_-]*)\s*=\s*[{"]([^}"]*)[}"]|([A-Za-z][A-Za-z0-9_-]*)\s*=\s*([^,\s]+)', fields):
            name = fm.group(1) or fm.group(3)
            val = fm.group(2) if fm.group(2) is not None else fm.group(4)
            rec[safe(name).lower()] = safe(val).strip()
        rec["file_id"] = rec.get("file") or rec.get("filename") or rec.get("bib_key") or ""
        records.append(rec)
    if not records:
        return None, "bibtex_catalog_error: no entries matched"
    return records, None


def parse_ris_catalog(path):
    try:
        raw = read_text_file(path, max_bytes=TEXT_READ_LIMIT)
    except Exception as e:
        return None, "ris_catalog_error: %s" % e
    records = []
    cur = None
    for line in raw.splitlines():
        if len(line) < 6 or line[4] != "-":
            continue
        tag = line[0:2].strip()
        val = line[6:].strip()
        if tag == "TY":
            cur = {"entry_type": val}
            records.append(cur)
        elif tag == "ER":
            cur = None
        elif cur is not None:
            cur[tag.lower()] = val
    for r in records:
        r["file_id"] = r.get("l1") or r.get("url") or r.get("title") or r.get("id") or ""
    if not records:
        return None, "ris_catalog_error: no records"
    return records, None


def _qid(ident):
    return '"' + ident.replace('"', '""') + '"'


def load_sqlite_catalog(path):
    records = []
    tables = []
    con = None
    try:
        uri_path = urllib.parse.quote(os.path.abspath(path))
        con = sqlite3.connect("file:" + uri_path + "?mode=ro", uri=True)
    except Exception:
        try:
            con = sqlite3.connect(path)
        except Exception as e:
            return None, "sqlite_open_error: %s" % e
    try:
        con.execute("PRAGMA query_only=ON")
        cur = con.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        for t in tables[:20]:
            try:
                rows = cur.execute("SELECT * FROM %s LIMIT 1000" % _qid(t)).fetchall()
                cols = [d[0] for d in cur.description]
                for row in rows:
                    rec = {}
                    for c, v in zip(cols, row):
                        rec[safe(c)] = safe(v) if isinstance(v, (str, bytes)) else v
                    rec["_table"] = safe(t)
                    records.append(rec)
            except Exception:
                continue
    except Exception as e:
        return None, "sqlite_query_error: %s" % e
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
    if not tables:
        return [], "sqlite_no_tables"
    return records, None


def collect_catalogs(entries, root, errors):
    catalogs = []
    for e in entries:
        base = os.path.basename(e["path"])
        if base in ("case.json", "task.md"):
            continue
        if e.get("type") != "file":
            continue
        ext = os.path.splitext(e["path"])[1].lower()
        if ext not in CATALOG_EXTENSIONS:
            continue
        locator = e["path"]
        abs_path = os.path.join(root, locator.replace("/", os.sep))
        if ext == ".json":
            records, err = parse_json_catalog(abs_path)
        elif ext == ".csv":
            records, err = parse_csv_catalog(abs_path)
        elif ext in (".bib", ".bibtex"):
            records, err = parse_bibtex_catalog(abs_path)
        elif ext == ".ris":
            records, err = parse_ris_catalog(abs_path)
        else:
            records, err = load_sqlite_catalog(abs_path)
        if err:
            errors.append("catalog %s: %s" % (locator, err))
        norm = [normalize_record(r, locator) for r in (records or [])]
        catalogs.append({
            "kind": ext.lstrip("."), "path": locator, "error": err,
            "record_count": len(norm), "records": norm,
        })
    return catalogs


# ---------------------------------------------------------------------------
# reconciliation
# ---------------------------------------------------------------------------

def reconcile(entries, catalogs):
    if not catalogs:
        return {
            "available": False, "matches": [], "mismatches": [],
            "missing": [], "orphaned": [], "note": "no library catalog detected",
        }
    by_rel = {e["path"]: e for e in entries}
    by_name = defaultdict(list)
    for e in entries:
        by_name[os.path.basename(e["path"])].append(e)

    matches, mismatches, missing = [], [], []
    seen = set()
    for cat in catalogs:
        for rec in cat["records"]:
            ident = rec.get("file_id")
            loc = rec.get("locator")
            if not ident:
                missing.append({"record": safe(str(rec.get("raw"))[:120]), "locator": loc,
                                "reason": "no_file_identifier"})
                continue
            ident_norm = ident.replace("\\", "/")
            found = by_rel.get(ident_norm)
            if found is None:
                base = os.path.basename(ident_norm)
                cands = by_name.get(base, [])
                if len(cands) == 1:
                    found = cands[0]
            if found is None:
                missing.append({"record_id": ident, "locator": loc, "reason": "file_not_found"})
                continue
            seen.add(found["path"])
            ok = True
            reasons = []
            rec_hash = rec.get("hash")
            rec_size = rec.get("size")
            if rec_hash and found.get("sha256"):
                if rec_hash.lower() != found["sha256"].lower():
                    ok = False
                    reasons.append("hash_mismatch")
            if rec_size is not None and found.get("size") is not None:
                if int(rec_size) != found["size"]:
                    ok = False
                    reasons.append("size_mismatch")
            if ok:
                matches.append({"path": found["path"], "locator": loc})
            else:
                mismatches.append({
                    "path": found["path"], "locator": loc, "reasons": reasons,
                    "catalog_hash": (rec_hash or "")[:16],
                    "file_hash": (found.get("sha256") or "")[:16],
                })

    orphaned = [e["path"] for e in entries if e.get("type") == "file" and e["path"] not in seen]
    orphaned.sort()
    return {
        "available": True, "matches": matches, "mismatches": mismatches,
        "missing": missing, "orphaned": orphaned,
    }


# ---------------------------------------------------------------------------
# index + mechanical Q&A
# ---------------------------------------------------------------------------

def build_index(entries, dup_groups, chains):
    path_to_dup = {}
    for k, paths in dup_groups.items():
        for p in paths:
            path_to_dup[p] = k[:16]
    path_to_chain = {}
    for chain_id, chain in chains.items():
        for m in chain["members"]:
            path_to_chain[m["path"]] = chain_id
    index = []
    for e in entries:
        if e.get("type") != "file":
            continue
        index.append({
            "path": e["path"],
            "name": os.path.basename(e["path"]),
            "ext": os.path.splitext(e["path"])[1].lower(),
            "size": e.get("size"),
            "mtime_iso": e.get("mtime_iso"),
            "sha256": (e.get("sha256") or "")[:16],
            "sha256_full": e.get("sha256"),
            "mode": e.get("mode"),
            "status": e.get("status"),
            "dup_group": path_to_dup.get(e["path"]),
            "version_chain": path_to_chain.get(e["path"]),
        })
    index.sort(key=lambda x: x["path"])
    return index


def build_summary(entries, dir_entries, dup, chains, aliases, catalogs, recon):
    files = [e for e in entries if e.get("type") == "file"]
    ext_counts = defaultdict(int)
    for e in files:
        ext_counts[os.path.splitext(e["path"])[1].lower() or "(none)"] += 1
    largest = sorted(files, key=lambda e: -(e.get("size") or 0))[:10]
    return {
        "file_count": len(files),
        "directory_count": len(dir_entries),
        "symlink_count": len(aliases["symlinks"]),
        "broken_symlink_count": len(aliases["broken_symlinks"]),
        "hardlink_group_count": len(aliases["hardlink_groups"]),
        "duplicate_group_count": len(dup),
        "duplicate_files_in_groups": sum(len(v) for v in dup.values()),
        "version_candidate_count": len(chains),
        "catalog_count": len(catalogs),
        "catalog_record_count": sum(c["record_count"] for c in catalogs),
        "files_by_extension": dict(sorted(ext_counts.items())),
        "largest_files": [{"path": e["path"], "size": e.get("size")} for e in largest],
        "reconciliation_counts": {
            "matches": len(recon.get("matches", [])),
            "mismatches": len(recon.get("mismatches", [])),
            "missing": len(recon.get("missing", [])),
            "orphaned": len(recon.get("orphaned", [])),
        },
    }


def metadata_table(catalogs):
    """Flatten catalog records carrying author/year/title for search/QA."""
    rows = []
    for cat in catalogs:
        for rec in cat["records"]:
            raw = rec.get("raw", {})
            author = raw.get("author") or raw.get("authors") or raw.get("au")
            year = raw.get("year") or raw.get("date") or raw.get("py") or raw.get("created")
            title = raw.get("title") or raw.get("ti") or raw.get("filename") or raw.get("file")
            if author or year or title:
                rows.append({
                    "locator": rec.get("locator"),
                    "file_id": rec.get("file_id"),
                    "author": safe(str(author)) if author else None,
                    "year": safe(str(year)) if year else None,
                    "title": safe(str(title)) if title else None,
                })
    rows.sort(key=lambda r: (r["locator"] or "", r["file_id"] or ""))
    return rows


# ---------------------------------------------------------------------------
# result assembly
# ---------------------------------------------------------------------------

def build_result(args, case_obj, case_err, task_text, inventory, dup, chains,
                 aliases, version_notes, catalogs, recon, index, summary, metadata,
                 sources, uncertainty, errors, degradations, change_log, started, status):
    case_id = extract_case_id(case_obj, task_text)
    contract_key, contract = discover_contract(case_obj)
    payload = build_contract_payload(contract) if contract else {}

    finished = time.time()
    runtime = {
        "started_at": _dt.datetime.fromtimestamp(started, tz=_dt.timezone.utc).isoformat(),
        "finished_at": now_iso(),
        "seconds": round(finished - started, 3),
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }

    result = {
        "case_id": case_id,
        "status": status,
        "sources": sources,
        "uncertainty": uncertainty,
        "pending_human": [],
        "change_log": change_log,
        "errors": errors,
        "degradations": degradations,
        "runtime": runtime,
        "limitations": [],
        "computed": {
            "contract_key": contract_key,
            "contract_schema": contract,
            "summary": summary,
            "inventory": {
                "root": inventory.get("root"),
                "files": [e for e in inventory["entries"] if e.get("type") == "file"],
                "dirs": inventory["dirs"],
                "symlinks": [e for e in inventory["entries"] if e.get("type") == "symlink"],
                "others": [e for e in inventory["entries"] if e.get("type") not in ("file", "symlink")],
            },
            "duplicate_groups": dup,
            "version_candidates": chains,
            "version_notes": version_notes,
            "aliases": aliases,
            "catalogs": [
                {"kind": c["kind"], "path": c["path"], "error": c["error"],
                 "record_count": c["record_count"]}
                for c in catalogs
            ],
            "reconciliation": recon,
            "index": index,
            "metadata": metadata,
            "artifacts": list(ARTIFACT_NAMES),
        },
    }

    # echo contract fields verbatim at top level; collide with reserved -> computed._contract_<k>
    if contract:
        for field in sorted(contract.keys()):
            val = payload.get(field)
            if field in RESERVED_KEYS:
                result["computed"].setdefault("_contract_%s" % field, val)
            else:
                result[field] = val
    return result


def build_skeleton(args, case_obj, case_err, task_text, started):
    case_id = extract_case_id(case_obj, task_text)
    contract_key, contract = discover_contract(case_obj)
    payload = build_contract_payload(contract) if contract else {}
    status = "partial"
    errors = []
    if case_err:
        errors.append(case_err)
        if "not_found" in case_err:
            status = "input_error"
    change_log = [{
        "time": now_iso(), "actor": "skill", "step": "skeleton",
        "detail": "partial result skeleton written before analysis",
    }]
    result = {
        "case_id": case_id, "status": status,
        "sources": [], "uncertainty": [], "pending_human": [],
        "change_log": change_log, "errors": errors, "degradations": [],
        "runtime": {
            "started_at": _dt.datetime.fromtimestamp(started, tz=_dt.timezone.utc).isoformat(),
            "finished_at": now_iso(),
            "seconds": round(time.time() - started, 3),
            "python": sys.version.split()[0], "platform": sys.platform,
        },
        "limitations": [], "computed": {
            "contract_key": contract_key, "contract_schema": contract,
            "summary": None, "artifacts": [],
        },
    }
    if contract:
        for field in sorted(contract.keys()):
            val = payload.get(field)
            if field in RESERVED_KEYS:
                result["computed"].setdefault("_contract_%s" % field, val)
            else:
                result[field] = val
    return result


def compute_status(inventory, errors, degradations, case_err):
    if case_err:
        return "input_error"
    material_files = [e for e in inventory["entries"]
                      if e.get("type") == "file" and e.get("role") != "task_meta"]
    if not material_files:
        return "insufficient_evidence"
    # Any processing error (catalog parse failure, inventory read error, ...) counts as a
    # degradation and downgrades the result to "partial"; case errors were handled above.
    if degradations or inventory["errors"] or any(not e.startswith("case_") for e in errors):
        return "partial"
    return "ok"


# ---------------------------------------------------------------------------
# artifact writing
# ---------------------------------------------------------------------------

def write_artifacts(args, inventory, dup, chains, aliases, version_notes, catalogs,
                    recon, index, summary, metadata):
    outdir = args.output_dir
    os.makedirs(outdir, exist_ok=True)
    artifacts = {}
    errors = []

    # 1. CSV manifest
    try:
        path = os.path.join(outdir, "library_manifest.csv")
        rows = []
        for e in inventory["entries"] + inventory["dirs"]:
            rows.append({
                "path": e["path"], "type": e.get("type"), "size": e.get("size"),
                "mtime_iso": e.get("mtime_iso"), "mode": e.get("mode"),
                "sha256": e.get("sha256") or "", "status": e.get("status"),
                "link_target": e.get("link_target") or "", "st_nlink": e.get("st_nlink") or "",
            })
        rows.sort(key=lambda r: (r["type"], r["path"]))
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [
                "path", "type", "size", "mtime_iso", "mode", "sha256", "status", "link_target", "st_nlink"])
            w.writeheader()
            for r in rows:
                w.writerow(r)
        artifacts["library_manifest.csv"] = path
    except Exception as e:
        errors.append("write library_manifest.csv failed: %s" % e)

    # 2. directory tree
    try:
        path = os.path.join(outdir, "directory_tree.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_tree(inventory["entries"], inventory["dirs"]) + "\n")
        artifacts["directory_tree.txt"] = path
    except Exception as e:
        errors.append("write directory_tree.txt failed: %s" % e)

    # 3. dedup + version + version notes + aliases
    try:
        path = os.path.join(outdir, "dedup_version.json")
        obj = {
            "duplicate_groups": dup,
            "version_candidates": chains,
            "version_notes": version_notes,
            "aliases": aliases,
        }
        write_json_atomic(path, obj)
        artifacts["dedup_version.json"] = path
    except Exception as e:
        errors.append("write dedup_version.json failed: %s" % e)

    # 4. recovery report
    try:
        path = os.path.join(outdir, "recovery_report.json")
        write_json_atomic(path, recon)
        artifacts["recovery_report.json"] = path
    except Exception as e:
        errors.append("write recovery_report.json failed: %s" % e)

    # 5. index
    try:
        path = os.path.join(outdir, "library_index.json")
        write_json_atomic(path, {"index": index, "metadata": metadata, "summary": summary})
        artifacts["library_index.json"] = path
    except Exception as e:
        errors.append("write library_index.json failed: %s" % e)

    # 6. handoff markdown report
    try:
        path = os.path.join(outdir, "library_manifest.md")
        lines = ["# 科研资料库整理交接报告", ""]
        lines.append("## 摘要")
        lines.append("- 文件数：%d" % summary["file_count"])
        lines.append("- 目录数：%d" % summary["directory_count"])
        lines.append("- 重复组：%d（共 %d 个文件）" % (summary["duplicate_group_count"], summary["duplicate_files_in_groups"]))
        lines.append("- 版本候选链：%d" % summary["version_candidate_count"])
        lines.append("- 符号链接：%d（断链 %d）" % (summary["symlink_count"], summary["broken_symlink_count"]))
        lines.append("")
        lines.append("## 目录树")
        lines.append("```text")
        lines.append(render_tree(inventory["entries"], inventory["dirs"]))
        lines.append("```")
        lines.append("")
        lines.append("## 对账结果")
        rc = summary["reconciliation_counts"]
        lines.append("- matches=%d mismatches=%d missing=%d orphaned=%d" % (
            rc["matches"], rc["mismatches"], rc["missing"], rc["orphaned"]))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        artifacts["library_manifest.md"] = path
    except Exception as e:
        errors.append("write library_manifest.md failed: %s" % e)

    # 7. operation log (JSONL)
    try:
        path = os.path.join(outdir, "operation_log.jsonl")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for step in build_log_steps(summary):
                f.write(json.dumps(step, ensure_ascii=False, sort_keys=True) + "\n")
        artifacts["operation_log.jsonl"] = path
    except Exception as e:
        errors.append("write operation_log.jsonl failed: %s" % e)

    return artifacts, errors


def build_log_steps(summary):
    t = now_iso()
    return [
        {"time": t, "actor": "skill", "step": "inventory",
         "detail": "scanned input recursively (files=%d, dirs=%d)" % (summary["file_count"], summary["directory_count"])},
        {"time": t, "actor": "skill", "step": "dedup",
         "detail": "content SHA-256 duplicate groups: %d" % summary["duplicate_group_count"]},
        {"time": t, "actor": "skill", "step": "versions",
         "detail": "heuristic version candidate chains: %d" % summary["version_candidate_count"]},
        {"time": t, "actor": "skill", "step": "reconciliation",
         "detail": "matches/mismatches/missing/orphaned computed"},
        {"time": t, "actor": "skill", "step": "index",
         "detail": "searchable index rebuilt (%d files)" % summary["file_count"]},
    ]


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------

def build_sources(inventory, dup, chains, aliases, recon):
    sources = []
    # duplicates
    for sha, paths in dup.items():
        sources.append({
            "item": "duplicate group %s" % sha[:16],
            "locator": "; ".join(paths),
            "quote": "sha256=%s" % sha,
        })
    # version chains
    for cid, chain in chains.items():
        locs = [m["path"] for m in chain["members"]]
        sources.append({
            "item": "version candidate chain %s" % cid,
            "locator": "; ".join(locs),
            "quote": " -> ".join("%s:%s" % (m["path"].split("/")[-1], m["sha256"]) for m in chain["members"]),
        })
    # broken symlinks
    for p in aliases["broken_symlinks"]:
        sources.append({
            "item": "broken symlink", "locator": p, "quote": "symlink target unresolved",
        })
    # reconciliation mismatches
    for mm in recon.get("mismatches", []):
        sources.append({
            "item": "reconciliation mismatch", "locator": mm["path"],
            "quote": "catalog_hash=%s file_hash=%s reasons=%s" % (
                mm.get("catalog_hash"), mm.get("file_hash"), ",".join(mm.get("reasons", []))),
        })
    return sources


# ---------------------------------------------------------------------------
# CLI pipeline
# ---------------------------------------------------------------------------

def run_full(args, started):
    change_log = []
    case_obj, case_err = load_case(args.case_json)

    try:
        task_text = read_text_file(args.task_path, max_bytes=TEXT_READ_LIMIT) if os.path.isfile(args.task_path) else None
    except OSError:
        task_text = None

    cfg = {"max_hash_bytes": args.max_hash_bytes}
    inventory = build_inventory(args.input_root, cfg)
    errors = list(inventory["errors"])
    degradations = list(inventory["degradations"])

    # append case errors
    if case_err:
        errors.append(case_err)

    if args.skeleton_only:
        result = build_skeleton(args, case_obj, case_err, task_text, started)
        os.makedirs(args.output_dir, exist_ok=True)
        try:
            write_json_atomic(os.path.join(args.output_dir, "result.json"), result)
        except Exception as e:
            print("skeleton write failed: %s" % e, file=sys.stderr)
            return 2
        print("skeleton written: %s (status=%s)" % (os.path.join(args.output_dir, "result.json"), result["status"]))
        return 0

    # mechanical layers
    dup = group_duplicates(inventory["entries"])
    chains = group_versions(inventory["entries"])
    aliases = detect_aliases(inventory["entries"])
    version_notes = align_version_notes(parse_version_notes(task_text), inventory["entries"])
    catalogs = collect_catalogs(inventory["entries"], inventory["root"], errors)
    recon = reconcile(inventory["entries"], catalogs)
    index = build_index(inventory["entries"], dup, chains)
    summary = build_summary(inventory["entries"], inventory["dirs"], dup, chains, aliases, catalogs, recon)
    metadata = metadata_table(catalogs)

    sources = build_sources(inventory, dup, chains, aliases, recon)
    uncertainty = []
    if chains:
        uncertainty.append({
            "item": "version_candidates", "reason": "stem normalization + mtime ordering is heuristic; confirm each chain manually",
            "severity": "medium",
        })
    if catalogs:
        uncertainty.append({
            "item": "catalog_parsing", "reason": "BibTeX/RIS/CSV/SQLite parsing is best-effort; nested braces and malformed rows may be partial",
            "severity": "low",
        })

    status = compute_status(inventory, errors, degradations, case_err)

    artifacts, write_errs = write_artifacts(
        args, inventory, dup, chains, aliases, version_notes, catalogs, recon, index, summary, metadata)
    errors.extend(write_errs)

    change_log.append({
        "time": now_iso(), "actor": "skill", "step": "full_pipeline",
        "detail": "inventory+dedup+versions+aliases+catalogs+reconciliation+index+artifacts",
    })

    result = build_result(
        args, case_obj, case_err, task_text, inventory, dup, chains, aliases,
        version_notes, catalogs, recon, index, summary, metadata, sources,
        uncertainty, errors, degradations, change_log, started, status)

    # honest downgrade: if any artifact failed, reflect partial
    if write_errs and result["status"] == "ok":
        result["status"] = "partial"
        result["degradations"].append("some artifacts could not be written: %d" % len(write_errs))

    os.makedirs(args.output_dir, exist_ok=True)
    try:
        write_json_atomic(os.path.join(args.output_dir, "result.json"), result)
    except Exception as e:
        print("result write failed: %s" % e, file=sys.stderr)
        return 2

    print("finalize: %d files, %d dup groups, %d version candidates, %d broken symlinks, %d reconcile mismatches -> %s" % (
        summary["file_count"], summary["duplicate_group_count"], summary["version_candidate_count"],
        summary["broken_symlink_count"], summary["reconciliation_counts"]["mismatches"],
        os.path.join(args.output_dir, "result.json")))
    return 0


def run_update(args):
    if not args.update or not args.result:
        print("--update requires PATCH.json and --result RESULT.json", file=sys.stderr)
        return 2
    try:
        result = read_json(args.result)
    except Exception as e:
        print("cannot load result for update: %s" % e, file=sys.stderr)
        return 2
    try:
        patch = read_json(args.update)
    except Exception as e:
        print("cannot load patch: %s" % e, file=sys.stderr)
        return 2
    deep_merge(result, patch)
    result.setdefault("change_log", []).append({
        "time": now_iso(), "actor": "agent", "step": "update",
        "detail": "semantic patch merged: %s" % ", ".join(sorted(patch.keys())),
    })
    try:
        write_json_atomic(args.result, result)
    except Exception as e:
        print("update write failed: %s" % e, file=sys.stderr)
        return 2
    print("updated: %s" % args.result)
    return 0


def verify_manifest(input_root, outdir, max_bytes):
    manifest_path = os.path.join(outdir, "library_manifest.csv")
    if not os.path.isfile(manifest_path):
        return None
    rows = {}
    with open(manifest_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("type") == "file" and row.get("sha256"):
                rows[row["path"]] = row["sha256"]
    mismatches, missing = [], []
    for rel, expected in rows.items():
        abs_p = os.path.join(input_root, rel.replace("/", os.sep))
        if not os.path.isfile(abs_p):
            missing.append(rel)
            continue
        try:
            st = os.stat(abs_p)
            if st.st_size <= max_bytes:
                h, _ = sha256_file(abs_p)
                if h != expected:
                    mismatches.append(rel)
        except OSError:
            mismatches.append(rel)
    # coverage: every input file present in manifest
    actual = set()
    for dirpath, _dn, filenames in os.walk(input_root):
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), input_root).replace(os.sep, "/")
            actual.add(rel)
    coverage_missing_rows = sorted(actual - set(rows.keys()))
    return {
        "manifest_missing_files": sorted(missing),
        "hash_mismatches": sorted(mismatches),
        "coverage_missing_rows": coverage_missing_rows,
    }


def run_check(args):
    result_path = args.check
    problems = []
    try:
        result = read_json(result_path)
    except Exception as e:
        print("CHECK FAIL: cannot parse %s: %s" % (result_path, e))
        return 2

    if not result.get("case_id"):
        problems.append("case_id missing/empty")
    if result.get("status") not in ALLOWED_STATUS:
        problems.append("invalid status %r" % result.get("status"))

    case_obj, _ = load_case(args.case_json)
    if case_obj:
        _, contract = discover_contract(case_obj)
        if contract:
            missing_fields = []
            for field in contract:
                if field in result:
                    continue
                comp = result.get("computed")
                if isinstance(comp, dict) and ("_contract_" + field) in comp:
                    continue
                missing_fields.append(field)
            if missing_fields:
                problems.append("contract fields missing: %s" % ", ".join(missing_fields))

    outdir = os.path.dirname(result_path) or args.output_dir
    for art in ARTIFACT_NAMES:
        p = os.path.join(outdir, art)
        if not os.path.isfile(p):
            problems.append("artifact missing: %s" % art)

    verify = None
    if args.input_root and os.path.isdir(args.input_root):
        verify = verify_manifest(args.input_root, outdir, args.max_hash_bytes)
        if verify and (verify["hash_mismatches"] or verify["coverage_missing_rows"]):
            problems.append("manifest verification: %d hash mismatches, %d missing rows" % (
                len(verify["hash_mismatches"]), len(verify["coverage_missing_rows"])))

    if problems:
        print("CHECK FAIL:")
        for p in problems:
            print(" -", p)
        return 2
    print("CHECK OK: case_id=%s status=%s artifacts=%d" % (
        result.get("case_id"), result.get("status"), len(ARTIFACT_NAMES)))
    return 0


def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="document-library repair pipeline (deterministic, standard-library only)",
    )
    p.add_argument("--case-json", default="/input/case.json")
    p.add_argument("--task-path", default="/input/task.md")
    p.add_argument("--input-root", default="/input")
    p.add_argument("--output-dir", default="/app/output")
    p.add_argument("--work-dir", default="/app/work")
    p.add_argument("--max-hash-bytes", type=int, default=DEFAULT_MAX_HASH_BYTES)
    p.add_argument("--skeleton-only", action="store_true",
                   help="write only a partial result skeleton, then exit 0")
    p.add_argument("--update", metavar="PATCH.json",
                   help="merge an Agent-authored semantic patch into an existing result.json")
    p.add_argument("--result", metavar="RESULT.json", default="/app/output/result.json")
    p.add_argument("--check", metavar="RESULT.json",
                   help="validate a result.json (structure + contract + artifacts + hashes)")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    started = time.time()
    if args.check:
        return run_check(args)
    if args.update:
        return run_update(args)
    return run_full(args, started)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:  # last-resort guard: never crash into an empty output dir
        print("fatal: %s" % e, file=sys.stderr)
        sys.exit(2)
