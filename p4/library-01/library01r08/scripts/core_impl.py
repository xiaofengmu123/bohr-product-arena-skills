#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — document library repair pipeline (Python 3.8+, stdlib only).

Reads a messy research document library under an input root and, read-only,
produces a verifiable, re-runnable inventory plus repair records:

  * inventory: files, directories, permissions, symlinks (incl. broken) and
    hardlink alias groups;
  * content SHA-256 hashing with a size ceiling and honest `hashed=false`
    markers;
  * duplicate groups by content hash (not filename);
  * candidate version chains from normalized stem + modification time + content
    difference (heuristic, labelled as such);
  * administrative version-note parsing and alignment (linked / orphan);
  * library catalog parsing (JSON / CSV / BibTeX / RIS / SQLite) and
    reconciliation (matches / mismatches / missing / orphaned);
  * a rebuilt searchable index and mechanical retrieval answers;
  * editable artifacts plus `result.json` that fills every field of the task
    `output_contract` and records status, sources, uncertainty and change log.

All source files remain read-only. Deterministic traversal order and stable
hashes make repeated runs identical except timestamps.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import stat
import sys
import time
import traceback
from collections import OrderedDict, defaultdict

try:
    from datetime import datetime, timezone
except Exception:  # pragma: no cover - very old Python fallback
    from datetime import datetime

    timezone = None

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_HASH_SIZE = int(os.environ.get("LIBRARY_MAX_HASH_SIZE", str(128 * 1024 * 1024)))
MAX_TEXT_SIZE = int(os.environ.get("LIBRARY_MAX_TEXT_SIZE", str(2 * 1024 * 1024)))
MAX_CATALOG_SIZE = int(os.environ.get("LIBRARY_MAX_CATALOG_SIZE", str(32 * 1024 * 1024)))

RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "runtime", "limitations",
    "computed", "artifacts",
}
VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}


# ---------------------------------------------------------------------------
# small deterministic helpers
# ---------------------------------------------------------------------------
def utc_now():
    if timezone is not None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def iso_from_epoch(t):
    try:
        if timezone is not None:
            return datetime.fromtimestamp(t, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return datetime.utcfromtimestamp(t).replace(microsecond=0).isoformat() + "Z"
    except (OSError, OverflowError, ValueError):
        return None


def permission_string(mode):
    bits = [(stat.S_IRUSR, "r"), (stat.S_IWUSR, "w"), (stat.S_IXUSR, "x"),
            (stat.S_IRGRP, "r"), (stat.S_IWGRP, "w"), (stat.S_IXGRP, "x"),
            (stat.S_IROTH, "r"), (stat.S_IWOTH, "w"), (stat.S_IXOTH, "x")]
    out = []
    for bit, ch in bits:
        out.append(ch if (mode & bit) else "-")
    return "".join(out)


def file_type_label(st_mode):
    if stat.S_ISREG(st_mode):
        return "regular"
    if stat.S_ISDIR(st_mode):
        return "directory"
    if stat.S_ISLNK(st_mode):
        return "symlink"
    if stat.S_ISFIFO(st_mode):
        return "fifo"
    if stat.S_ISSOCK(st_mode):
        return "socket"
    if stat.S_ISBLK(st_mode):
        return "block_device"
    if stat.S_ISCHR(st_mode):
        return "char_device"
    return "unknown"


def ext_of(name):
    base = os.path.basename(name)
    dot = base.rfind(".")
    if dot <= 0:
        return ""
    return base[dot + 1:].lower()


def stem_of(name):
    base = os.path.basename(name)
    dot = base.rfind(".")
    if dot <= 0:
        return base
    return base[:dot]


# ---------------------------------------------------------------------------
# text / json readers (all degrade gracefully)
# ---------------------------------------------------------------------------
def read_text_safe(path, limit=MAX_TEXT_SIZE):
    try:
        size = os.path.getsize(path)
        if size > limit:
            return None, "too_large_for_text"
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        return None, "read_error: %s" % e
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "decoded_with_replacement"


def load_json_safe(path, limit=MAX_CATALOG_SIZE):
    try:
        size = os.path.getsize(path)
        if size > limit:
            return None, "too_large_for_catalog"
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        return None, "read_error: %s" % e
    try:
        return json.loads(raw.decode("utf-8-sig")), None
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as e:
        return None, "json_error: %s" % e


# ---------------------------------------------------------------------------
# contract discovery
# ---------------------------------------------------------------------------
def find_contract(data):
    """Locate the task output_contract under any common key name."""
    if not isinstance(data, dict):
        return None
    for key in ("output_contract", "output", "result_schema", "contract",
                "outputContract", "resultSchema"):
        if key in data and data[key] is not None:
            return data[key]
    for val in data.values():
        if isinstance(val, dict) and find_contract(val) is not None:
            return find_contract(val)
    return None


def normalize_contract(contract):
    """Return an ordered mapping field -> declared type/default (best effort)."""
    out = OrderedDict()
    if isinstance(contract, dict):
        for k, v in contract.items():
            if isinstance(v, dict):
                out[str(k)] = v.get("type", v.get("default", None))
            else:
                out[str(k)] = v
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                out[item] = None
            elif isinstance(item, dict):
                name = item.get("name", item.get("field", item.get("key")))
                if name is not None:
                    out[str(name)] = item.get("type", item.get("default", None))
    return out


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------
def sha256_file(path, limit=MAX_HASH_SIZE):
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, "stat_error: %s" % e
    if size > limit:
        return None, "too_large"
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest(), None
    except OSError as e:
        return None, "hash_error: %s" % e


def record_entry(full, root):
    rel = os.path.relpath(full, root).replace(os.sep, "/")
    rec = OrderedDict()
    rec["path"] = rel
    rec["name"] = os.path.basename(full)
    rec["extension"] = ext_of(rel)
    rec["is_symlink"] = False
    rec["symlink_target"] = None

    if os.path.islink(full):
        rec["kind"] = "symlink"
        rec["is_symlink"] = True
        try:
            rec["symlink_target"] = os.readlink(full)
        except OSError as e:
            rec["symlink_error"] = "readlink_error: %s" % e
        try:
            st = os.stat(full)
            rec["resolved"] = True
            rec["size"] = st.st_size
            rec["mtime"] = iso_from_epoch(st.st_mtime)
            rec["mode_octal"] = oct(stat.S_IMODE(st.st_mode))
            rec["permissions"] = permission_string(st.st_mode)
            if stat.S_ISREG(st.st_mode):
                digest, err = sha256_file(full)
                rec["sha256"] = digest
                rec["hashed"] = err is None
                if err:
                    rec["hash_reason"] = err
        except OSError as e:
            rec["broken"] = True
            rec["resolved"] = False
            rec["error"] = "broken_symlink: %s" % e
        return rec

    try:
        st = os.stat(full)
    except OSError as e:
        rec["kind"] = "unreadable"
        rec["error"] = "stat_error: %s" % e
        return rec

    rec["size"] = st.st_size
    rec["mtime"] = iso_from_epoch(st.st_mtime)
    rec["mode_octal"] = oct(stat.S_IMODE(st.st_mode))
    rec["permissions"] = permission_string(st.st_mode)
    rec["dev"] = getattr(st, "st_dev", 0)
    rec["inode"] = getattr(st, "st_ino", 0)
    rec["nlink"] = getattr(st, "st_nlink", 1)
    rec["file_type"] = file_type_label(st.st_mode)

    if not stat.S_ISREG(st.st_mode):
        rec["kind"] = "non_regular"
        return rec

    rec["kind"] = "regular"
    digest, err = sha256_file(full)
    rec["sha256"] = digest
    rec["hashed"] = err is None
    if err:
        rec["hash_reason"] = err
    text, terr = read_text_safe(full) if st.st_size <= MAX_TEXT_SIZE else (None, "too_large_for_text")
    rec["text_available"] = terr is None
    if terr:
        rec["text_reason"] = terr
    return rec, text


def walk_input(root):
    files = []
    dirs = []
    errors = []

    def onerror(err):
        errors.append({"path": getattr(err, "filename", root), "error": str(err)})

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=onerror, followlinks=False):
        dirnames.sort()
        filenames.sort()
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        try:
            st = os.stat(dirpath)
            dirs.append(OrderedDict([
                ("path", rel_dir),
                ("mode_octal", oct(stat.S_IMODE(st.st_mode))),
                ("permissions", permission_string(st.st_mode)),
                ("mtime", iso_from_epoch(st.st_mtime)),
                ("is_symlink", os.path.islink(dirpath)),
            ]))
        except OSError as e:
            errors.append({"path": rel_dir, "error": "dir_stat_error: %s" % e})
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            got = record_entry(full, root)
            if isinstance(got, tuple):
                rec, text = got
                if text is not None:
                    rec["_text"] = text
            else:
                rec = got
            files.append(rec)
    return files, dirs, errors


# ---------------------------------------------------------------------------
# duplicate groups (content hash)
# ---------------------------------------------------------------------------
def group_duplicates(files):
    by_hash = defaultdict(list)
    for rec in files:
        if rec.get("hashed") and rec.get("sha256"):
            by_hash[rec["sha256"]].append(rec["path"])
    groups = []
    for digest in sorted(by_hash):
        paths = sorted(by_hash[digest])
        if len(paths) > 1:
            groups.append({"sha256": digest, "paths": paths, "count": len(paths)})
    return groups


# ---------------------------------------------------------------------------
# version candidates (heuristic)
# ---------------------------------------------------------------------------
_VERSION_TOKEN = re.compile(
    r"[\s._-]*("
    r"(?:v|V|ver|version)\s*[\d]+(?:\.[\d]+){0,3}"
    r"(?:[-_.]?(?:alpha|beta|rc|final|draft|rev|r)[\d]*)?"
    r"|(?:final|draft|old|new|copy[\d]*|backup[\d]*|rev[\d]*|r[\d]+)"
    r"|(?:副本|旧|新|初稿|终稿|最终|定稿)"
    r")",
    re.IGNORECASE,
)
_DATE_TOKEN = re.compile(r"[\s._-]?(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:[0-3]\d)?")


def normalize_stem(name):
    base = os.path.basename(name)
    stem = stem_of(base)
    stem = _VERSION_TOKEN.sub(" ", stem)
    stem = _DATE_TOKEN.sub(" ", stem)
    stem = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", " ", stem.lower())
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem


def _size_delta(a, b):
    sa, sb = a.get("size"), b.get("size")
    if isinstance(sa, int) and isinstance(sb, int):
        return sb - sa
    return None


def build_version_candidates(files, dups):
    dup_paths = set()
    for g in dups:
        dup_paths.update(g["paths"])
    by_stem = defaultdict(list)
    for rec in files:
        if rec.get("kind") == "regular" and rec.get("hashed"):
            stem = normalize_stem(rec["name"])
            if stem:
                by_stem[stem].append(rec)
    chains = []
    for stem in sorted(by_stem):
        members = by_stem[stem]
        if len(members) < 2:
            continue
        members_sorted = sorted(members, key=lambda r: (r.get("mtime") or "", r["path"]))
        transitions = []
        for i in range(len(members_sorted) - 1):
            a, b = members_sorted[i], members_sorted[i + 1]
            if a.get("sha256") == b.get("sha256"):
                relation = "identical"
            else:
                relation = "modified"
            transitions.append({
                "from": a["path"], "to": b["path"],
                "from_sha256": a.get("sha256"), "to_sha256": b.get("sha256"),
                "size_delta": _size_delta(a, b),
                "from_mtime": a.get("mtime"), "to_mtime": b.get("mtime"),
                "relation": relation,
            })
        chain = {
            "stem": stem,
            "heuristic": True,
            "paths": [r["path"] for r in members_sorted],
            "sha256s": [r.get("sha256") for r in members_sorted],
            "mtime_sorted": [r.get("mtime") for r in members_sorted],
            "transitions": transitions,
        }
        if any(t["relation"] == "modified" for t in transitions):
            chains.append(chain)
    return chains


# ---------------------------------------------------------------------------
# administrative version notes
# ---------------------------------------------------------------------------
_NOTE_LINE = re.compile(
    r"(?:版本|version|v\s*\d|ver\.?|修订|修订号|revision|rev\.?|release)",
    re.IGNORECASE,
)
_NOTE_VERSION = re.compile(r"(?:v|ver\.?|version|版本|修订号|revision|rev\.?)\s*[:#]?\s*([0-9][0-9A-Za-z.\-_]*)",
                          re.IGNORECASE)
_NOTE_DATE = re.compile(r"(?:19|20)\d{2}[-/.](?:0[1-9]|1[0-2])[-/.](?:[0-3]\d)?")


def extract_version_notes(text, source):
    notes = []
    if not text:
        return notes
    for ln, line in enumerate(text.splitlines(), 1):
        if not _NOTE_LINE.search(line):
            continue
        vm = _NOTE_VERSION.search(line)
        dm = _NOTE_DATE.search(line)
        note = OrderedDict([
            ("source", source),
            ("line", ln),
            ("quote", line.strip()[:400]),
            ("version_label", vm.group(1) if vm else None),
            ("date", dm.group(0) if dm else None),
        ])
        notes.append(note)
    return notes


def align_notes(notes, chains):
    linked = []
    orphan = []
    chain_index = {}
    for ci, ch in enumerate(chains):
        for p in ch["paths"]:
            chain_index[os.path.basename(p).lower()] = ci
        chain_index[ch["stem"]] = ci
    for note in notes:
        hit = None
        low = (note.get("quote") or "").lower()
        for name, ci in chain_index.items():
            if name and name in low:
                hit = ci
                break
        if hit is None:
            orphan.append(note)
        else:
            linked.append({"chain_index": hit, "note": note})
    return linked, orphan


# ---------------------------------------------------------------------------
# catalog parsing
# ---------------------------------------------------------------------------
def _csv_rows(path):
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            return [OrderedDict(r) for r in reader], None
    except (OSError, csv.Error) as e:
        return [], "csv_error: %s" % e


def _bib_entries(text):
    entries = []
    for m in re.finditer(r"@(\w+)\s*\{([^,]+),", text):
        etype = m.group(1).lower()
        key = m.group(2).strip()
        entries.append(OrderedDict([("type", etype), ("key", key)]))
    return entries


def _ris_entries(text):
    entries = []
    cur = None
    for line in text.splitlines():
        if len(line) >= 6 and line[2:4] == "  " and line[4:6] == "- ":
            tag, value = line[:2].strip(), line[6:].strip()
            if tag in ("TY",):
                if cur:
                    entries.append(cur)
                cur = OrderedDict()
            if cur is not None:
                cur[tag] = value if tag not in cur else cur[tag] + "; " + value
    if cur:
        entries.append(cur)
    return entries


def _sqlite_entries(path):
    entries = []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
        try:
            cur = con.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cur.fetchall()]
            for t in tables:
                try:
                    cur.execute("SELECT * FROM %s LIMIT 200" % t)
                    cols = [d[0] for d in cur.description]
                    for row in cur.fetchall():
                        entries.append(OrderedDict([("table", t)] + list(zip(cols, row))))
                except sqlite3.Error as e:
                    entries.append(OrderedDict([("table", t), ("query_error", str(e))]))
        finally:
            con.close()
        return entries, None
    except (sqlite3.Error, OSError) as e:
        return [], "sqlite_error: %s" % e


def parse_catalog(path):
    ext = ext_of(path)
    if ext in ("json", "jsonl"):
        data, err = load_json_safe(path)
        if err:
            return "json", [], err
        if isinstance(data, dict):
            for key in ("files", "items", "entries", "records", "documents", "library"):
                if isinstance(data.get(key), list):
                    return "json", list(data[key]), None
            return "json", [data], None
        if isinstance(data, list):
            return "json", list(data), None
        return "json", [], "json_not_mappable"
    if ext == "csv":
        rows, err = _csv_rows(path)
        return "csv", rows, err
    if ext in ("bib", "bibtex"):
        text, err = read_text_safe(path)
        if err:
            return "bibtex", [], err
        return "bibtex", _bib_entries(text), None
    if ext in ("ris",):
        text, err = read_text_safe(path)
        if err:
            return "ris", [], err
        return "ris", _ris_entries(text), None
    if ext in ("sqlite", "sqlite3", "db"):
        entries, err = _sqlite_entries(path)
        return "sqlite", entries, err
    return None, [], None


def _entry_identity(entry):
    if isinstance(entry, dict):
        for k in ("path", "file", "filename", "name", "key", "title", "id"):
            if k in entry and entry[k] not in (None, ""):
                return str(entry[k])
        return json.dumps(entry, ensure_ascii=False, sort_keys=True)
    return str(entry)


def reconcile(catalog_entries, files):
    file_paths = {r["path"] for r in files}
    file_names = {os.path.basename(r["path"]).lower(): r["path"] for r in files}
    file_hashes = {}
    for r in files:
        if r.get("sha256"):
            file_hashes.setdefault(r["sha256"], []).append(r["path"])
    matches, mismatches, missing, orphaned = [], [], [], []
    seen_paths = set()
    for entry in catalog_entries:
        ident = _entry_identity(entry)
        base = os.path.basename(ident).lower() if ident else ""
        target = None
        if ident in file_paths:
            target = ident
        elif base in file_names:
            target = file_names[base]
        if target:
            if target in seen_paths:
                mismatches.append({"entry": entry, "file": target, "reason": "already_matched"})
            else:
                matches.append({"entry": entry, "file": target})
                seen_paths.add(target)
        else:
            missing.append({"entry": entry, "expected": ident})
    matched = seen_paths
    for r in files:
        if r["path"] not in matched:
            orphaned.append({"file": r["path"], "size": r.get("size"), "sha256": r.get("sha256")})
    return {
        "catalog_entry_count": len(catalog_entries),
        "matches": matches,
        "mismatches": mismatches,
        "missing": missing,
        "orphaned": orphaned,
        "counts": {"matches": len(matches), "mismatches": len(mismatches),
                   "missing": len(missing), "orphaned": len(orphaned)},
    }


# ---------------------------------------------------------------------------
# index + mechanical answers
# ---------------------------------------------------------------------------
def tokenize(text):
    return [t for t in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", (text or "").lower()) if t]


def build_index(files, dirs, catalogs):
    index = OrderedDict()
    index["by_path"] = {r["path"]: r for r in files}
    index["by_name"] = {}
    index["by_extension"] = defaultdict(list)
    index["tokens"] = defaultdict(set)
    for r in files:
        index["by_name"][os.path.basename(r["path"]).lower()] = r["path"]
        index["by_extension"][r.get("extension") or ""].append(r["path"])
        for tok in tokenize(r["name"]):
            index["tokens"][tok].add(r["path"])
        text = r.get("_text")
        if text:
            for tok in tokenize(text)[:2000]:
                index["tokens"][tok].add(r["path"])
    for c in catalogs:
        for entry in c.get("entries", []):
            if isinstance(entry, dict):
                ident = _entry_identity(entry)
                for tok in tokenize(ident):
                    index["tokens"][tok].add(ident)
    index["tokens"] = OrderedDict((k, sorted(v)) for k, v in sorted(index["tokens"].items()))
    index["by_extension"] = OrderedDict((k, sorted(v)) for k, v in sorted(index["by_extension"].items()))
    index["by_name"] = OrderedDict(sorted(index["by_name"].items()))
    index["by_path"] = OrderedDict(sorted(index["by_path"].items()))
    return index


def _count_by_token(index, tok):
    return len(index["tokens"].get(tok, set()))


def answer_questions(questions, ctx):
    answers = []
    if not questions:
        return answers
    if isinstance(questions, dict):
        questions = [{"id": k, "question": v} for k, v in questions.items()]
    for q in questions:
        if not isinstance(q, dict):
            q = {"question": str(q)}
        text = str(q.get("question", q.get("query", q.get("text", "")))).lower()
        qid = q.get("id", "q%d" % len(answers))
        ans = OrderedDict([("id", qid), ("question", q.get("question", q.get("query", ""))),
                           ("answer", None), ("evidence", [])])
        files = ctx["files"]
        if re.search(r"(重复|duplicate|dedup)", text):
            ans["answer"] = "duplicate_groups=%d" % len(ctx["duplicate_groups"])
            ans["evidence"] = [{"item": "duplicates", "locator": "computed.duplicate_groups",
                                "quote": "content-SHA256 groups"}]
        elif re.search(r"(版本|version)", text):
            ans["answer"] = "version_candidates=%d" % len(ctx["version_candidates"])
            ans["evidence"] = [{"item": "versions", "locator": "computed.version_candidates",
                                "quote": "heuristic stem+content chains"}]
        elif re.search(r"(断链|broken.*link|symlink)", text):
            broken = [f["path"] for f in files if f.get("broken")]
            ans["answer"] = "broken_symlinks=%d" % len(broken)
            if broken:
                ans["evidence"] = [{"item": "broken_symlinks", "locator": broken[0],
                                    "quote": broken[0]}]
        elif re.search(r"(缺失|missing)", text):
            ans["answer"] = "missing=%d" % ctx["reconciliation"]["counts"]["missing"]
            ans["evidence"] = [{"item": "missing", "locator": "computed.reconciliation.missing",
                                "quote": str(ctx["reconciliation"]["counts"]["missing"])}]
        elif re.search(r"(多少|count|total|number)", text):
            ans["answer"] = "files=%d dirs=%d" % (len(files), len(ctx["dirs"]))
            ans["evidence"] = [{"item": "counts", "locator": "computed.summary",
                                "quote": "files=%d dirs=%d" % (len(files), len(ctx["dirs"]))}]
        else:
            tok = tokenize(text)
            paths = set()
            for t in tok:
                if len(t) > 1:
                    paths.update(ctx["index"]["tokens"].get(t, set()))
            if paths:
                ans["answer"] = "matched_paths=%d" % len(paths)
                ans["evidence"] = [{"item": "index", "locator": "computed.index.tokens",
                                    "quote": "; ".join(sorted(paths)[:10])}]
            else:
                ans["answer"] = "no_match"
        answers.append(ans)
    return answers


# ---------------------------------------------------------------------------
# artifacts
# ---------------------------------------------------------------------------
def write_manifest_csv(path, files):
    fieldnames = ["path", "name", "extension", "kind", "size", "mtime", "permissions",
                  "mode_octal", "sha256", "hashed", "hash_reason", "is_symlink",
                  "symlink_target", "inode", "nlink", "broken", "error"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in files:
            row = {k: r.get(k) for k in fieldnames}
            w.writerow(row)


def write_tree(path, root, dirs, files):
    lines = []
    top = os.path.basename(os.path.abspath(root)) or "input"
    lines.append(top + "/")
    dir_map = {d["path"]: d for d in dirs}
    file_map = defaultdict(list)
    for r in files:
        parent = os.path.dirname(r["path"])
        file_map[parent].append(r)
    for d in dirs:
        if d["path"] == ".":
            continue
        depth = d["path"].count("/") + 1
        label = os.path.basename(d["path"])
        children = file_map.get(d["path"], []) + [x["path"] for x in dirs if os.path.dirname(x["path"]) == d["path"]]
        marker = "  [空目录]" if not children else ""
        lines.append("  " * depth + label + "/" + marker)
    for r in sorted(files, key=lambda x: x["path"]):
        depth = r["path"].count("/") + 1
        annot = ""
        if r.get("broken"):
            annot = "  [断链]"
        elif r.get("is_symlink"):
            annot = "  -> %s" % r.get("symlink_target", "")
        elif r.get("kind") == "non_regular":
            annot = "  [非常规:%s]" % r.get("file_type", "")
        elif r.get("hashed") is False:
            annot = "  [%s]" % r.get("hash_reason", "未哈希")
        lines.append("  " * depth + os.path.basename(r["path"]) + annot)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def write_operation_log(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n")


def write_artifacts(outdir, ctx):
    os.makedirs(outdir, exist_ok=True)
    paths = {}
    paths["library_manifest.csv"] = os.path.join(outdir, "library_manifest.csv")
    paths["directory_tree.txt"] = os.path.join(outdir, "directory_tree.txt")
    paths["dedup_version.json"] = os.path.join(outdir, "dedup_version.json")
    paths["recovery_report.json"] = os.path.join(outdir, "recovery_report.json")
    paths["library_index.json"] = os.path.join(outdir, "library_index.json")
    paths["library_manifest.md"] = os.path.join(outdir, "library_manifest.md")
    paths["operation_log.jsonl"] = os.path.join(outdir, "operation_log.jsonl")

    write_manifest_csv(paths["library_manifest.csv"], ctx["files"])
    write_tree(paths["directory_tree.txt"], ctx["input_root"], ctx["dirs"], ctx["files"])

    dedup = OrderedDict([
        ("duplicate_groups", ctx["duplicate_groups"]),
        ("version_candidates", ctx["version_candidates"]),
        ("version_notes", ctx["version_notes"]),
        ("version_note_alignment", {"linked": ctx["linked_notes"], "orphan": ctx["orphan_notes"]}),
        ("aliases", ctx["aliases"]),
    ])
    with open(paths["dedup_version.json"], "w", encoding="utf-8") as fh:
        json.dump(dedup, fh, ensure_ascii=False, indent=2, default=str)

    with open(paths["recovery_report.json"], "w", encoding="utf-8") as fh:
        json.dump(ctx["reconciliation"], fh, ensure_ascii=False, indent=2, default=str)

    with open(paths["library_index.json"], "w", encoding="utf-8") as fh:
        json.dump(ctx["index"], fh, ensure_ascii=False, indent=2, default=str)

    md = ["# 科研资料库交接报告", ""]
    md.append("## 概要")
    md.append("")
    md.append("- case_id: %s" % ctx.get("case_id", ""))
    md.append("- 文件数: %d" % len(ctx["files"]))
    md.append("- 目录数: %d" % len(ctx["dirs"]))
    md.append("- 重复组: %d" % len(ctx["duplicate_groups"]))
    md.append("- 版本候选链: %d" % len(ctx["version_candidates"]))
    md.append("- 断链符号链接: %d" % sum(1 for f in ctx["files"] if f.get("broken")))
    md.append("")
    md.append("## 目录树")
    md.append("")
    md.append("见 `directory_tree.txt`。")
    md.append("")
    md.append("## 说明")
    md.append("")
    md.append("版本候选链为启发式结果（归一化主干 + 修改时间 + 内容差异），需人工确认。")
    with open(paths["library_manifest.md"], "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")

    write_operation_log(paths["operation_log.jsonl"], ctx["change_log"])
    return paths


# ---------------------------------------------------------------------------
# result building
# ---------------------------------------------------------------------------
def fill_contract_value(name, ctx):
    n = str(name).lower()
    files = ctx.get("files", [])
    if n in ("case_id", "caseid"):
        return ctx.get("case_id")
    if any(k in n for k in ("file", "manifest", "inventory", "inventory_files")):
        return {"count": len(files), "items": files}
    if "count" in n or "total" in n:
        return len(files)
    if "summary" in n:
        return ctx.get("summary", {})
    if "duplicate" in n or "dedup" in n:
        return ctx.get("duplicate_groups", [])
    if "version" in n:
        return ctx.get("version_candidates", [])
    if "alias" in n or "hardlink" in n or "symlink" in n:
        return ctx.get("aliases", {"symlinks": [], "hardlink_groups": []})
    if any(k in n for k in ("reconcil", "recovery", "match")):
        return ctx.get("reconciliation", {})
    if "index" in n:
        return ctx.get("index", {})
    if "answer" in n or "question" in n or "qa" in n:
        return ctx.get("answers", [])
    if "tree" in n or "director" in n:
        return ctx.get("tree_text", "")
    if "log" in n or "change" in n:
        return ctx.get("change_log", [])
    if "source" in n:
        return ctx.get("sources", [])
    if "error" in n:
        return ctx.get("errors", [])
    return None


def build_result(ctx, contract, status):
    result = OrderedDict()
    result["case_id"] = ctx.get("case_id")
    result["status"] = status
    result["sources"] = ctx["sources"]
    result["uncertainty"] = ctx["uncertainty"]
    result["pending_human"] = ctx["pending_human"]
    result["change_log"] = ctx["change_log"]
    result["errors"] = ctx["errors"]
    result["degradations"] = ctx["degradations"]
    result["runtime"] = ctx["runtime"]
    result["limitations"] = ctx["limitations"]
    result["computed"] = ctx["computed"]
    result["artifacts"] = ctx["artifacts"]

    for name, _decl in contract.items():
        value = fill_contract_value(name, ctx)
        if value is None:
            value = null_or_empty(name, _decl)
        if name in RESERVED_KEYS:
            result["computed"].setdefault("_contract_%s" % name, value)
        else:
            result[name] = value
    result["computed"]["_contract"] = contract
    return result


def null_or_empty(name, decl):
    if isinstance(decl, str) and decl in ("array", "list", "[]"):
        return []
    return None


# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------
def collect_aliases(files):
    symlinks = [r for r in files if r.get("is_symlink")]
    by_inode = defaultdict(list)
    for r in files:
        if r.get("inode") and r.get("kind") == "regular":
            by_inode[(r.get("dev"), r.get("inode"))].append(r["path"])
    hardlink_groups = [sorted(v) for v in by_inode.values() if len(v) > 1]
    return {"symlinks": symlinks, "hardlink_groups": hardlink_groups}


def load_questions(data):
    if not isinstance(data, dict):
        return []
    for key in ("questions", "queries", "queries_list", "asks"):
        if isinstance(data.get(key), list):
            return data[key]
        if isinstance(data.get(key), dict):
            return data[key]
    return []


def run_pipeline(input_root, output_dir, work_dir, case_json_path=None, task_path=None,
                 skeleton_only=False):
    started = time.time()
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    ctx = OrderedDict()
    ctx["input_root"] = input_root
    ctx["change_log"] = []
    ctx["sources"] = []
    ctx["uncertainty"] = []
    ctx["pending_human"] = []
    ctx["errors"] = []
    ctx["degradations"] = []
    ctx["limitations"] = []
    ctx["answers"] = []

    def log(step, detail):
        ctx["change_log"].append({"time": utc_now(), "actor": "scripts/core_impl.py",
                                  "step": step, "detail": detail})

    log("start", "input=%s output=%s work=%s" % (input_root, output_dir, work_dir))

    # --- case.json / contract discovery ---
    case_data = None
    case_id = None
    case_input_error = False
    contract = OrderedDict()
    if case_json_path and os.path.isfile(case_json_path):
        case_data, err = load_json_safe(case_json_path)
        if err:
            case_input_error = True
            ctx["errors"].append({"item": "case.json", "error": err})
            ctx["degradations"].append("case.json unreadable: %s" % err)
        if isinstance(case_data, dict):
            case_id = case_data.get("case_id") or case_data.get("id") or case_data.get("caseId")
            raw_contract = find_contract(case_data)
            if raw_contract is not None:
                contract = normalize_contract(raw_contract)
                log("contract", "found output_contract with %d fields" % len(contract))
            else:
                ctx["uncertainty"].append({
                    "item": "output_contract", "severity": "medium",
                    "reason": "case.json has no recognized output_contract key"})
        else:
            case_input_error = True
            ctx["errors"].append({"item": "case.json", "error": "not_a_dict"})
    else:
        case_input_error = True
        ctx["errors"].append({"item": "case.json", "error": "missing"})

    if not case_id:
        if task_path and os.path.isfile(task_path):
            text, _ = read_text_safe(task_path)
            if text:
                m = re.search(r"challenge[_-]?id[:`\s]*([A-Za-z0-9_-]+)", text)
                if m:
                    case_id = m.group(1)
        if not case_id:
            case_id = os.path.basename(os.path.normpath(input_root)) or "unknown"
    ctx["case_id"] = case_id

    # --- task.md text (org rules + administrative version notes) ---
    task_text = None
    if task_path and os.path.isfile(task_path):
        task_text, terr = read_text_safe(task_path)
        if terr:
            ctx["degradations"].append("task.md text: %s" % terr)

    # --- skeleton mode: early partial result, then exit ---
    if skeleton_only:
        ctx["files"] = []
        ctx["dirs"] = []
        ctx["duplicate_groups"] = []
        ctx["version_candidates"] = []
        ctx["aliases"] = {"symlinks": [], "hardlink_groups": []}
        ctx["reconciliation"] = {"matches": [], "mismatches": [], "missing": [], "orphaned": [],
                                 "counts": {"matches": 0, "mismatches": 0, "missing": 0, "orphaned": 0}}
        ctx["index"] = {"tokens": {}, "by_name": {}, "by_path": {}, "by_extension": {}}
        ctx["tree_text"] = ""
        ctx["summary"] = OrderedDict([("files", 0), ("dirs", 0), ("duplicate_groups", 0),
                                      ("version_candidates", 0)])
        ctx["computed"] = OrderedDict([
            ("files", []), ("dirs", []), ("summary", ctx["summary"]),
            ("inventory", []), ("duplicate_groups", []), ("version_candidates", []),
            ("version_notes", []), ("aliases", ctx["aliases"]),
            ("reconciliation", ctx["reconciliation"]),
            ("answers", []), ("index", ctx["index"]), ("artifacts", {}),
        ])
        ctx["artifacts"] = {}
        ctx["runtime"] = OrderedDict([
            ("started_at", utc_now()), ("elapsed_seconds", round(time.time() - started, 3)),
            ("skeleton", True),
        ])
        ctx["limitations"].append("skeleton-only run; full inventory not yet executed")
        log("skeleton", "wrote partial result skeleton")
        result = build_result(ctx, contract, "partial")
        write_result(os.path.join(output_dir, "result.json"), result)
        return result

    # --- full inventory ---
    files, dirs, errors = walk_input(input_root)
    ctx["files"] = files
    ctx["dirs"] = dirs
    ctx["errors"].extend([{"item": e.get("path", ""), "error": e.get("error", "")} for e in errors])
    for f in files:
        if f.get("error"):
            ctx["errors"].append({"item": f.get("path"), "error": f.get("error")})
        if f.get("kind") == "non_regular":
            ctx["degradations"].append("non_regular file %s (%s)" % (f.get("path"), f.get("file_type")))
        if f.get("hashed") is False and f.get("hash_reason"):
            ctx["degradations"].append("unhashed file %s (%s)" % (f.get("path"), f.get("hash_reason")))
    log("inventory", "files=%d dirs=%d errors=%d" % (len(files), len(dirs), len(errors)))

    # --- duplicates / versions / aliases ---
    duplicate_groups = group_duplicates(files)
    version_candidates = build_version_candidates(files, duplicate_groups)
    aliases = collect_aliases(files)
    ctx["duplicate_groups"] = duplicate_groups
    ctx["version_candidates"] = version_candidates
    ctx["aliases"] = aliases
    log("dedup_version", "dup_groups=%d version_chains=%d hardlink_groups=%d"
        % (len(duplicate_groups), len(version_candidates), len(aliases["hardlink_groups"])))

    # --- version notes ---
    version_notes = extract_version_notes(task_text or "", "task.md")
    # also scan small note/readme/manifest files in the input
    for f in files:
        low = f["name"].lower()
        if f.get("kind") == "regular" and f.get("text_available") and (
                "version" in low or "readme" in low or "note" in low or "说明" in low
                or "manifest" in low or "版本" in low):
            text, _ = read_text_safe(os.path.join(input_root, f["path"]))
            if text:
                version_notes.extend(extract_version_notes(text, f["path"]))
    linked_notes, orphan_notes = align_notes(version_notes, version_candidates)
    ctx["version_notes"] = version_notes
    ctx["linked_notes"] = linked_notes
    ctx["orphan_notes"] = orphan_notes
    log("version_notes", "notes=%d linked=%d orphan=%d"
        % (len(version_notes), len(linked_notes), len(orphan_notes)))

    # --- catalogs + reconciliation ---
    catalogs = []
    catalog_found = False
    for f in files:
        if f.get("kind") != "regular":
            continue
        ext = f.get("extension")
        if ext in ("json", "jsonl", "csv", "bib", "bibtex", "ris", "sqlite", "sqlite3", "db"):
            kind, entries, err = parse_catalog(os.path.join(input_root, f["path"]))
            if kind:
                catalog_found = True
                catalogs.append({"path": f["path"], "kind": kind, "entries": entries,
                                 "error": err, "entry_count": len(entries)})
                if err:
                    ctx["errors"].append({"item": f["path"], "error": err})
    reconciliation = {"catalogs": catalogs}
    if catalog_found:
        all_entries = []
        for c in catalogs:
            all_entries.extend(c.get("entries", []))
        reconciliation.update(reconcile(all_entries, files))
    else:
        reconciliation.update({"matches": [], "mismatches": [], "missing": [], "orphaned": [],
                               "counts": {"matches": 0, "mismatches": 0, "missing": 0, "orphaned": 0},
                               "note": "no_catalog_found"})
        ctx["degradations"].append("no library catalog (JSON/CSV/BibTeX/RIS/SQLite) found")
    ctx["reconciliation"] = reconciliation
    log("reconcile", "catalogs=%d %s" % (len(catalogs), reconciliation.get("counts", {})))

    # --- index + answers ---
    index = build_index(files, dirs, catalogs)
    ctx["index"] = index
    questions = load_questions(case_data or {})
    if task_text:
        for m in re.finditer(r"问题[:：]\s*(.+)", task_text):
            questions = list(questions) + [{"id": "task_q%d" % len(questions), "question": m.group(1)}]
    answers = answer_questions(questions, ctx)
    ctx["answers"] = answers
    log("index_answers", "tokens=%d questions=%d" % (len(index["tokens"]), len(answers)))

    # --- summary + evidence ---
    n_broken = sum(1 for f in files if f.get("broken"))
    n_unhashed = sum(1 for f in files if f.get("hashed") is False)
    summary = OrderedDict([
        ("files", len(files)), ("directories", len(dirs)),
        ("regular_files", sum(1 for f in files if f.get("kind") == "regular")),
        ("symlinks", len(aliases["symlinks"])),
        ("broken_symlinks", n_broken),
        ("hardlink_groups", len(aliases["hardlink_groups"])),
        ("duplicate_groups", len(duplicate_groups)),
        ("version_candidates", len(version_candidates)),
        ("version_notes", len(version_notes)),
        ("catalogs", len(catalogs)),
        ("reconciliation", reconciliation.get("counts", {})),
        ("unhashed_files", n_unhashed),
        ("errors", len(ctx["errors"])),
        ("degradations", len(ctx["degradations"])),
    ])
    ctx["summary"] = summary

    ctx["sources"].append({
        "item": "inventory",
        "locator": "computed.files",
        "quote": "files=%d dirs=%d (read-only os.walk + stat)" % (len(files), len(dirs)),
    })
    ctx["sources"].append({
        "item": "duplicates",
        "locator": "computed.duplicate_groups",
        "quote": "content-SHA256 groups=%d" % len(duplicate_groups),
    })
    if task_text:
        ctx["sources"].append({
            "item": "task_rules",
            "locator": task_path or "task.md",
            "quote": (task_text.strip().splitlines()[0] if task_text.strip() else "")[:400],
        })

    # --- artifacts ---
    tree_file = os.path.join(output_dir, "directory_tree.txt")
    artifacts = write_artifacts(output_dir, ctx)
    ctx["artifacts"] = {k: os.path.relpath(v, output_dir).replace(os.sep, "/")
                        for k, v in artifacts.items()}

    ctx["tree_text"] = ""
    try:
        with open(artifacts["directory_tree.txt"], encoding="utf-8") as fh:
            ctx["tree_text"] = fh.read()
    except OSError:
        pass

    ctx["computed"] = OrderedDict([
        ("files", files),
        ("dirs", dirs),
        ("summary", summary),
        ("duplicate_groups", duplicate_groups),
        ("version_candidates", version_candidates),
        ("version_notes", version_notes),
        ("version_note_alignment", {"linked": linked_notes, "orphan": orphan_notes}),
        ("aliases", aliases),
        ("reconciliation", reconciliation),
        ("answers", answers),
        ("index", index),
    ])

    ctx["runtime"] = OrderedDict([
        ("started_at", utc_now()),
        ("elapsed_seconds", round(time.time() - started, 3)),
        ("skeleton", False),
        ("python", sys.version.split()[0]),
    ])

    # status decision
    control_basenames = {"case.json", "task.md", "resources.yaml", "GITHUB_URL.txt"}
    material_files = [f for f in files if os.path.basename(f.get("path", "")) not in control_basenames]
    subdirs = [d for d in dirs if d.get("path") != "."]
    if case_input_error:
        status = "input_error"
    elif not material_files and not subdirs:
        status = "insufficient_evidence"
    elif not ctx["errors"]:
        status = "ok"
    else:
        status = "partial"
    ctx["limitations"].append(
        "version_candidates are heuristic (normalized stem + mtime + content diff); "
        "administrative confirmation required before archival.")
    log("finalize", "status=%s files=%d" % (status, len(files)))

    result = build_result(ctx, contract, status)
    write_result(os.path.join(output_dir, "result.json"), result)
    return result


def write_result(path, result):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# update / check / selftest
# ---------------------------------------------------------------------------
def deep_merge(base, patch):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def update_result(patch_path, result_path):
    if not os.path.isfile(result_path):
        print("update: result not found at %s" % result_path, file=sys.stderr)
        return 2
    patch, err = load_json_safe(patch_path)
    if err:
        print("update: cannot read patch: %s" % err, file=sys.stderr)
        return 2
    result, err = load_json_safe(result_path)
    if err:
        print("update: cannot read result: %s" % err, file=sys.stderr)
        return 2
    if not isinstance(result, dict) or not isinstance(patch, dict):
        print("update: result and patch must be JSON objects", file=sys.stderr)
        return 2
    deep_merge(result, patch)
    if "change_log" not in result:
        result["change_log"] = []
    result["change_log"].append({"time": utc_now(), "actor": "scripts/core_impl.py --update",
                                 "step": "semantic_update", "detail": "applied patch from %s" % patch_path})
    write_result(result_path, result)
    print("update: merged %d patch keys into %s" % (len(patch), result_path))
    return 0


def check_result(result_path, case_json_path=None):
    failures = []
    if not os.path.isfile(result_path):
        print("check: result not found: %s" % result_path)
        return 2
    result, err = load_json_safe(result_path)
    if err:
        print("check: invalid JSON: %s" % err)
        return 1
    if not isinstance(result, dict):
        print("check: result is not a JSON object")
        return 1
    case_id = result.get("case_id")
    if not case_id or not isinstance(case_id, str):
        failures.append("case_id missing or empty")
    if result.get("status") not in VALID_STATUS:
        failures.append("status invalid: %r" % result.get("status"))
    for key in ("sources", "uncertainty", "pending_human", "change_log", "errors",
                "degradations", "runtime", "limitations", "computed"):
        if key not in result:
            failures.append("missing required key: %s" % key)
    if not isinstance(result.get("change_log"), list):
        failures.append("change_log must be a list")
    if case_json_path and os.path.isfile(case_json_path):
        data, cerr = load_json_safe(case_json_path)
        if cerr is None and isinstance(data, dict):
            contract = normalize_contract(find_contract(data) or {})
            for name in contract:
                if name not in result and "_contract_%s" % name not in result.get("computed", {}):
                    failures.append("contract field not filled: %s" % name)
    for name in failures:
        print("check: FAIL %s" % name)
    if failures:
        print("check: %d failure(s)" % len(failures))
        return 1
    print("check: OK case_id=%s status=%s" % (case_id, result.get("status")))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_arg_parser():
    p = argparse.ArgumentParser(prog="core_impl.py",
                                description="document library repair pipeline")
    p.add_argument("--input", "--input-root", dest="input", default=DEFAULT_INPUT)
    p.add_argument("--output", "--output-dir", dest="output", default=DEFAULT_OUTPUT)
    p.add_argument("--work", "--work-dir", dest="work", default=DEFAULT_WORK)
    p.add_argument("--case-json", dest="case_json", default=os.path.join(DEFAULT_INPUT, "case.json"))
    p.add_argument("--task-path", dest="task_path", default=os.path.join(DEFAULT_INPUT, "task.md"))
    p.add_argument("--skeleton-only", action="store_true")
    p.add_argument("--check", dest="check")
    p.add_argument("--update", dest="update")
    p.add_argument("--result", dest="result")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    try:
        if args.check:
            return check_result(args.check, args.case_json)
        if args.update:
            if not args.result:
                print("--update requires --result RESULT", file=sys.stderr)
                return 2
            return update_result(args.update, args.result)
        run_pipeline(args.input, args.output, args.work,
                     case_json_path=args.case_json, task_path=args.task_path,
                     skeleton_only=args.skeleton_only)
        return 0
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception:
        traceback.print_exc()
        print("fatal error; a partial skeleton may still be written", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
