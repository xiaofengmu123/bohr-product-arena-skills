"""core_impl.py - deterministic document-library repair pipeline (stdlib only).

Design goals
------------
1. Early partial persistence: the very first action of a full run writes a legal
   ``result.json`` skeleton (status=partial) before any inventory work, so a
   timeout or crash can never leave the harness with an empty output directory.
2. Deterministic outputs: recursive traversal is lexicographically sorted, hash
   grouping follows sorted paths, and no randomness is used. Re-runs differ only
   in wall-clock timestamps recorded in ``change_log``/``runtime``.
3. Honest degradation: oversized, unreadable, non-regular and broken-link files
   are recorded, never silently dropped; bad JSON/encoding are captured as
   errors and processing continues.
4. Pure standard library: ``hashlib``, ``json``, ``csv``, ``sqlite3``, ``re``,
   ``pathlib``, ``os``, ``stat``, ``argparse``, ``datetime``.

Result contract (top-level, always present)
-------------------------------------------
case_id, status, sources, uncertainty, pending_human, change_log, errors,
degradations, runtime, limitations, computed, artifacts -- plus every field of
the case ``output_contract`` echoed verbatim. Reserved-name collisions are moved
to ``computed._contract_<field>`` instead of being overwritten.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import stat as statmod
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

__version__ = "1.1.0"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_INPUT = Path("/input")
DEFAULT_OUTPUT = Path("/app/output")
DEFAULT_WORK = Path("/app/work")
RESULT_NAME = "result.json"
HASH_CHUNK = 1024 * 1024
DEFAULT_MAX_HASH_BYTES = 2 * 1024 * 1024  # 2 MiB content-hash ceiling

RESERVED_FIELDS = {
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "runtime", "limitations",
    "computed", "artifacts",
}

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

# Version-stem normalizers: strip version/draft/date noise so that the same
# logical document lands in one candidate group. These are heuristics and are
# labelled as such in the output.
_RE_VERSION = re.compile(
    r"[_\-\s.]*(v|ver|rev|r|version|版本|修订|draft|草稿|final|最终|final_)?"
    r"[_\-\s.]*\d+(\.\d+)*[a-z]?[_\-\s.]*",
    re.IGNORECASE,
)
_RE_DATE = re.compile(r"[_\-\s.]*(19|20)\d{2}[-_]?\d{0,2}[-_]?\d{0,2}[_\-\s.]*")
_RE_STATUS = re.compile(
    r"[_\-\s.]*(final|最终|draft|草稿|clean|copy|副本|backup|备份|old|旧)"
    r"[_\-\s.]*",
    re.IGNORECASE,
)

# Administrative version-note detection (best effort, quoted verbatim).
_RE_NOTE_LINE = re.compile(
    r"(版本|version|修订|rev|replaces|替代|supersed|deprecat|→|->|->)",
    re.IGNORECASE,
)
_RE_BIB_ENTRY = re.compile(
    r"@(?P<type>\w+)\s*\{\s*(?P<key>[^,]+),\s*(?P<body>.*?)\n\s*\}",
    re.DOTALL,
)
_RE_BIB_FIELD = re.compile(
    r"(\w+)\s*=\s*[{](.*?)[}]|(\w+)\s*=\s*[\"](.*?)[\"]|(\w+)\s*=\s*([^,\n]+)",
    re.DOTALL,
)
_RE_RIS_FIELD = re.compile(r"^([A-Z0-9]{2})\s+-\s+(.*)$")

QUESTION_KEYS = (
    ("count", re.compile(r"多少个|多少文件|files? count|how many|数量")),
    ("duplicates", re.compile(r"重复|duplicate|dedup")),
    ("missing", re.compile(r"缺失|missing|缺")),
    ("broken", re.compile(r"断链|断|broken|symlink")),
    ("versions", re.compile(r"版本|version")),
    ("authors", re.compile(r"作者|author")),
    ("years", re.compile(r"年份|year|年度")),
)


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------
def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def read_text_lenient(path: Path) -> str:
    """Return decoded text or '' with an error marker recorded by the caller."""
    if not path or not path.exists():
        return ""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def load_json_lenient(path: Path):
    """Load JSON; return (value, error_string). Never raises."""
    if not path or not path.exists():
        return None, "missing"
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        text = raw.decode("utf-8")
        return json.loads(text), None
    except UnicodeDecodeError:
        try:
            return json.loads(path.read_bytes().decode("utf-8-sig")), None
        except Exception as exc:  # noqa: BLE001
            return None, f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 - deliberate containment
        return None, f"{type(exc).__name__}: {exc}"


def sha256_of(path: Path, max_bytes: int) -> tuple:
    """Return (hexdigest, flag). flag is None on success, else a reason code.

    Only regular files are hashed here; callers guard non-regular paths.
    """
    try:
        size = path.stat().st_size
        if size > max_bytes:
            return "", "too_large"
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(HASH_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest(), None
    except PermissionError:
        return "", "unreadable"
    except OSError as exc:
        return "", f"os_error:{exc.strerror or exc}"


def relpath_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def safe_mode(st) -> str:
    try:
        return statmod.filemode(st.st_mode)
    except Exception:  # noqa: BLE001
        return "?"


def normalize_stem(name: str) -> str:
    stem, _dot, ext = name.rpartition(".")
    if not stem:
        stem = name
    s = _RE_VERSION.sub("", stem)
    s = _RE_DATE.sub("", s)
    s = _RE_STATUS.sub("", s)
    s = re.sub(r"[_\-\s.]+", "_", s).strip("_")
    return s.lower() if s else name.lower()


def flatten_keys(obj, prefix="", out=None):
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else k
            flatten_keys(v, key, out)
    else:
        out[prefix] = obj
    return out


def find_contract(case_obj, case_path: Path):
    """Locate the output_contract under any conventional key.

    Returns (contract_value, contract_key_name). The value is returned as-is;
    the harness copy step normalizes it below.
    """
    candidates = (
        "output_contract", "outputContract", "output", "result_schema",
        "resultSchema", "contract", "expected_output", "expectedOutput",
    )
    if isinstance(case_obj, dict):
        for key in candidates:
            if key in case_obj:
                return case_obj[key], key
        for key, val in case_obj.items():
            if isinstance(val, dict) and key.lower().endswith("contract"):
                return val, key
    return None, None


def extract_case_id(case_obj, case_path: Path) -> str:
    if isinstance(case_obj, dict):
        for key in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
            val = case_obj.get(key)
            if isinstance(val, str) and val:
                return val
    return ""


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------
def inventory(root: Path, max_hash_bytes: int):
    """Walk ``root`` deterministically and produce file/dir records.

    Returns (files, dirs, errors, degradations).
    """
    files, dirs, errors, degradations = [], [], [], []
    if not root.exists():
        errors.append({"code": "input_missing", "path": str(root),
                       "detail": "input root does not exist"})
        return files, dirs, errors, degradations

    for current, subdirs, names in os.walk(root):
        subdirs.sort()
        names.sort()
        cur = Path(current)
        for name in subdirs:
            p = cur / name
            dirs.append({"path": relpath_posix(p, root), "empty": False})
        for name in names:
            p = cur / name
            rel = relpath_posix(p, root)
            record = {"path": rel, "name": name, "type": "file"}
            try:
                lst = p.lstat()
                record["size"] = lst.st_size
                record["mtime"] = int(lst.st_mtime)
                record["mode"] = safe_mode(lst)
                record["uid"] = lst.st_uid if hasattr(lst, "st_uid") else None
                record["gid"] = lst.st_gid if hasattr(lst, "st_gid") else None
                record["inode"] = lst.st_ino if hasattr(lst, "st_ino") else None
                record["dev"] = lst.st_dev if hasattr(lst, "st_dev") else None
            except OSError as exc:
                files.append({
                    "path": rel, "name": name, "type": "file",
                    "size": None, "mtime": None, "mode": "?",
                    "hash": "", "hash_status": "stat_error",
                })
                errors.append({"code": "stat_error", "path": rel,
                               "detail": f"{type(exc).__name__}: {exc}"})
                continue

            if statmod.S_ISLNK(lst.st_mode):
                target = os.readlink(p) if hasattr(os, "readlink") else ""
                exists = p.exists()
                record.update({
                    "type": "symlink", "target": target,
                    "link_status": "ok" if exists else "broken",
                    "hash": "", "hash_status": "symlink",
                })
                files.append(record)
                if not exists:
                    degradations.append({
                        "code": "broken_symlink", "path": rel,
                        "detail": f"target missing: {target}",
                    })
                continue

            if not statmod.S_ISREG(lst.st_mode):
                record.update({"hash": "", "hash_status": "non_regular",
                               "type": "non_regular"})
                files.append(record)
                degradations.append({
                    "code": "non_regular", "path": rel,
                    "detail": "skipped content hash for non-regular file",
                })
                continue

            digest, flag = sha256_of(p, max_hash_bytes)
            record["hash"] = digest
            record["hash_status"] = flag or "ok"
            files.append(record)
            if flag:
                degradations.append({
                    "code": flag, "path": rel,
                    "detail": f"content hash skipped: {flag}",
                })
    return files, dirs, errors, degradations


def mark_empty_dirs(dirs, files):
    file_paths = {f["path"] for f in files}
    occupied = set()
    for p in file_paths:
        parts = p.split("/")
        for i in range(1, len(parts)):
            occupied.add("/".join(parts[:i]))
    for d in dirs:
        d["empty"] = d["path"] not in occupied
    return dirs


def build_tree(files, dirs):
    """Render a deterministic indented tree with [空目录] markers."""
    lines = ["/input"]
    children = {}
    for d in dirs:
        children.setdefault(d["path"], []).append(
            ("[空目录]" if d["empty"] else "", True))
    for f in files:
        parent = f["path"].rsplit("/", 1)[0] if "/" in f["path"] else ""
        label = f["name"] + (" (断链)" if f.get("link_status") == "broken" else "")
        children.setdefault(parent, []).append((label, False))

    def walk(node, depth):
        entries = children.get(node, [])
        for label, is_dir in sorted(entries, key=lambda t: t[0]):
            if is_dir:
                lines.append("  " * depth + label.rstrip("/") + "/")
                walk(node + "/" + label if node else label, depth + 1)
            else:
                lines.append("  " * depth + label)

    walk("", 1)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deduplication / versions / aliases
# ---------------------------------------------------------------------------
def deduplicate(files):
    by_hash = {}
    for f in files:
        if f.get("hash") and f.get("hash_status") == "ok":
            by_hash.setdefault(f["hash"], []).append(f["path"])
    groups = []
    for digest in sorted(by_hash):
        paths = sorted(by_hash[digest])
        if len(paths) > 1:
            groups.append({"sha256": digest, "count": len(paths),
                           "paths": paths, "evidence": "content-sha256"})
    return groups


def version_candidates(files):
    stem_groups = {}
    for f in files:
        if f.get("type") == "file" and f.get("hash_status") == "ok":
            stem = normalize_stem(f["name"])
            stem_groups.setdefault(stem, []).append(f)
    out = []
    for stem in sorted(stem_groups):
        members = sorted(
            stem_groups[stem],
            key=lambda f: (f.get("mtime") or 0, f["path"]),
        )
        if len(members) < 2:
            continue
        out.append({
            "normalized_stem": stem,
            "heuristic": "stem+mtime",
            "members": [
                {
                    "path": m["path"], "mtime": m.get("mtime"),
                    "sha256": m.get("hash"), "size": m.get("size"),
                } for m in members
            ],
            "replaces_chain": " → ".join(m["path"] for m in members),
            "note": "候选链按归一化主干+修改时间排序，需人工确认",
        })
    return out


def aliases(files):
    links = [{"type": "symlink", "path": f["path"], "target": f.get("target"),
              "status": f.get("link_status")}
             for f in files if f.get("type") == "symlink"]
    hard_groups = {}
    for f in files:
        if (f.get("type") == "file" and f.get("inode") is not None
                and f.get("hash_status") == "ok"):
            key = (f.get("dev"), f.get("inode"))
            hard_groups.setdefault(key, []).append(f["path"])
    hard_links = [
        {"type": "hardlink", "paths": sorted(v), "inode": k[1]}
        for k, v in sorted(hard_groups.items()) if len(v) > 1
    ]
    return {"symlinks": links, "hardlink_groups": hard_links}


def parse_version_notes(task_path: Path, case_path: Path, files,
                        input_root: Path):
    notes = []
    sources = []
    if task_path.exists():
        sources.append(("task.md", read_text_lenient(task_path)))
    if case_path.exists():
        sources.append(("case.json", read_text_lenient(case_path)))
    for label, text in sources:
        for lineno, line in enumerate(text.splitlines(), 1):
            if _RE_NOTE_LINE.search(line) and len(line.strip()) <= 300:
                notes.append({
                    "source": label, "line": lineno,
                    "raw": line.strip(),
                    "heuristic": True,
                })
    for f in files:
        if f.get("name", "").lower() in ("versions.json", "versions.csv",
                                         "version_notes.md", "changelog.md"):
            p = input_root / f["path"]
            if p.exists():
                text = read_text_lenient(p)
                for lineno, line in enumerate(text.splitlines(), 1):
                    if _RE_NOTE_LINE.search(line) and len(line.strip()) <= 300:
                        notes.append({
                            "source": f["path"], "line": lineno,
                            "raw": line.strip(), "heuristic": True,
                        })
    return notes


# ---------------------------------------------------------------------------
# Catalog parsing (JSON / CSV / BibTeX / RIS / SQLite)
# ---------------------------------------------------------------------------
def _catalogish(name: str) -> bool:
    low = name.lower()
    return any(k in low for k in
               ("library", "catalog", "manifest", "bibliograph", "zotero",
                "file", "metadata", "version"))


def parse_catalog(files, input_root: Path):
    records, catalogs, errors = [], [], []
    for f in files:
        if f.get("type") != "file":
            continue
        name = f["name"].lower()
        p = input_root / f["path"]
        if name.endswith(".json"):
            data, err = load_json_lenient(p)
            if err:
                # Any malformed JSON in the library tree is a defect we must
                # surface, whether or not its name looks catalog-like.
                errors.append({"code": "json_parse_error", "path": f["path"],
                               "detail": err})
                continue
            entries = []
            if isinstance(data, list):
                entries = data
            elif isinstance(data, dict):
                for key in ("files", "library", "entries", "items", "records"):
                    if isinstance(data.get(key), list):
                        entries = data[key]
                        break
                # A dict without a files-style list is metadata, not a catalog.
                if not entries and any(
                        k in data for k in ("path", "file", "sha256")):
                    entries = [data]
            if entries:
                catalogs.append({"path": f["path"], "format": "json",
                                 "entry_count": len(entries)})
                for e in entries:
                    if isinstance(e, dict):
                        records.append({"path": f["path"], "format": "json",
                                        "entry": e})
        elif name.endswith(".csv") and _catalogish(name):
            try:
                with open(p, newline="", encoding="utf-8-sig") as fh:
                    rows = list(csv.DictReader(fh))
            except Exception as exc:  # noqa: BLE001
                errors.append({"code": "csv_parse_error", "path": f["path"],
                               "detail": f"{type(exc).__name__}: {exc}"})
                continue
            catalogs.append({"path": f["path"], "format": "csv",
                             "entry_count": len(rows)})
            for r in rows:
                records.append({"path": f["path"], "format": "csv", "entry": r})
        elif name.endswith(".bib"):
            text = read_text_lenient(p)
            entries = []
            for m in _RE_BIB_ENTRY.finditer(text):
                fields = {}
                for fm in _RE_BIB_FIELD.finditer(m.group("body")):
                    key = next((g for g in fm.groups() if g), None)
                    val = next((g for g in fm.groups()
                                if g is not None and g != key), None)
                    if key:
                        fields[key.strip().lower()] = (val or "").strip()
                entries.append({"type": m.group("type"), "key": m.group("key"),
                                **fields})
            catalogs.append({"path": f["path"], "format": "bibtex",
                             "entry_count": len(entries)})
            for e in entries:
                records.append({"path": f["path"], "format": "bibtex",
                                "entry": e})
        elif name.endswith(".ris"):
            text = read_text_lenient(p)
            entry, entries = {}, []
            for line in text.splitlines():
                m = _RE_RIS_FIELD.match(line)
                if not m:
                    continue
                tag, val = m.group(1), m.group(2).strip()
                if tag == "ER" and entry:
                    entries.append(entry)
                    entry = {}
                elif tag in ("TY", "TI", "AU", "PY", "L1", "ID", "DO"):
                    entry[tag] = val
            catalogs.append({"path": f["path"], "format": "ris",
                             "entry_count": len(entries)})
            for e in entries:
                records.append({"path": f["path"], "format": "ris", "entry": e})
        elif name.endswith((".sqlite", ".db")):
            try:
                conn = sqlite3.connect(str(p))
                try:
                    tables = [r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")]
                    for t in tables:
                        rows = [dict(zip([c[1] for c in conn.execute(
                            f'PRAGMA table_info("{t}")')],
                            r)) for r in conn.execute(f'SELECT * FROM "{t}"')]
                        catalogs.append({"path": f["path"], "format": "sqlite",
                                         "table": t, "entry_count": len(rows)})
                        for r in rows:
                            records.append({"path": f["path"],
                                            "format": "sqlite", "entry": r})
                finally:
                    conn.close()
            except Exception as exc:  # noqa: BLE001
                errors.append({"code": "sqlite_error", "path": f["path"],
                               "detail": f"{type(exc).__name__}: {exc}"})
    return records, catalogs, errors


def reconcile(inventory_files, catalog_records):
    inventory_by_path = {f["path"]: f for f in inventory_files}
    declared_paths = set()
    mismatch, missing, orphaned, matches = [], [], [], []

    def record_entry(entry, src):
        keys = flatten_keys(entry)
        rpath = None
        for k in ("path", "file", "filepath", "file_path", "filename",
                  "L1", "location", "relative_path"):
            if keys.get(k):
                rpath = str(keys[k])
                break
        if not rpath:
            return
        norm = rpath.replace("\\", "/").strip("./")
        declared_paths.add(norm)
        inv = inventory_by_path.get(norm)
        decl_hash = (keys.get("sha256") or keys.get("hash")
                     or keys.get("content_hash") or "").lower()
        if inv is None:
            missing.append({"declared_path": norm, "source": src})
        elif decl_hash and inv.get("hash") and decl_hash != inv["hash"].lower():
            mismatch.append({"path": norm, "declared_hash": decl_hash,
                             "actual_hash": inv["hash"], "source": src})
        else:
            matches.append({"path": norm, "source": src})

    for rec in catalog_records:
        record_entry(rec.get("entry", {}), rec.get("path", ""))
    for f in inventory_files:
        if f["path"] not in declared_paths:
            orphaned.append({"path": f["path"],
                             "note": "未在任何库目录中声明"})
    return {
        "matches": matches,
        "mismatches": mismatch,
        "missing": missing,
        "orphaned": orphaned,
        "summary": {"matches": len(matches), "mismatches": len(mismatch),
                    "missing": len(missing), "orphaned": len(orphaned)},
    }


# ---------------------------------------------------------------------------
# Index and mechanical answers
# ---------------------------------------------------------------------------
def build_index(files, catalog_records, duplicate_groups, version_candidates):
    index = []
    for f in files:
        rec = {
            "path": f["path"], "type": f.get("type"),
            "size": f.get("size"), "mtime": f.get("mtime"),
            "sha256": f.get("hash"), "hash_status": f.get("hash_status"),
        }
        for key in ("target", "link_status", "mode"):
            if key in f:
                rec[key] = f[key]
        index.append(rec)
    for rec in catalog_records:
        entry = rec.get("entry", {})
        keys = flatten_keys(entry)
        rpath = None
        for k in ("path", "file", "filepath", "file_path", "filename", "L1",
                  "location", "relative_path"):
            if keys.get(k):
                rpath = str(keys[k])
                break
        if rpath:
            index.append({"path": rpath.replace("\\", "/").strip("./"),
                          "type": "catalog_entry", "source": rec.get("path"),
                          "authors": keys.get("author") or keys.get("AU"),
                          "year": keys.get("year") or keys.get("PY"),
                          "title": keys.get("title") or keys.get("TI"),
                          "doi": keys.get("doi") or keys.get("DO")})
    return index


def answer_questions(task_path: Path, case_path: Path, files, dirs,
                     duplicate_groups, version_candidates, aliases,
                     reconcile_result, catalog_records):
    text = ""
    if task_path.exists():
        text += read_text_lenient(task_path)
    if case_path.exists():
        text += read_text_lenient(case_path)
    answers = []
    n_hashed = sum(1 for f in files if f.get("hash_status") == "ok")
    broken = sum(1 for f in files if f.get("link_status") == "broken")
    for key, pattern in QUESTION_KEYS:
        if not pattern.search(text):
            continue
        if key == "count":
            ans = (f"盘点文件 {len(files)} 个，目录 {len(dirs)} 个，"
                   f"成功哈希 {n_hashed} 个")
        elif key == "duplicates":
            ans = f"按内容 SHA-256 发现 {len(duplicate_groups)} 组重复"
        elif key == "missing":
            ans = (f"库目录缺失条目 {reconcile_result['summary']['missing']} "
                   f"个（对账 missing）")
        elif key == "broken":
            ans = f"发现断链符号链接 {broken} 个"
        elif key == "versions":
            ans = f"识别版本候选链 {len(version_candidates)} 组（启发式）"
        elif key == "authors":
            authors = sorted({a for r in build_index(files, catalog_records,
                                                     [], [])
                              for a in ([r.get("authors")] if r.get("authors")
                                        else []) if a})
            ans = ("库目录作者字段: " + (", ".join(authors[:20])
                                          if authors else "not_reported"))
        else:  # years
            years = sorted({y for r in build_index(files, catalog_records,
                                                   [], [])
                            if r.get("year")})
            ans = ("库目录年份字段: " + (", ".join(str(y) for y in years[:20])
                                          if years else "not_reported"))
        answers.append({"question_key": key, "answer": ans,
                        "evidence": "inventory+reconcile（机械统计）"})
    return answers


# ---------------------------------------------------------------------------
# Skeleton / result assembly
# ---------------------------------------------------------------------------
def load_case_and_task(case_path: Path, task_path: Path):
    case_obj, case_err = load_json_lenient(case_path)
    task_text = read_text_lenient(task_path)
    return case_obj, case_err, task_text


def build_skeleton(case_obj, case_path: Path):
    contract, contract_key = find_contract(case_obj, case_path)
    case_id = extract_case_id(case_obj, case_path)
    return {
        "case_id": case_id,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "runtime": {},
        "limitations": [],
        "computed": {"_contract_key": contract_key, "files": [], "summary": {}},
        "artifacts": [],
    }, contract


def merge_contract(result, contract):
    """Echo every contract field verbatim; move reserved collisions."""
    if contract is None:
        result["uncertainty"].append({
            "item": "output_contract", "reason": "case.json 未提供 output_contract",
            "severity": "low",
        })
        return
    if isinstance(contract, dict):
        for key, value in contract.items():
            if key in RESERVED_FIELDS:
                result["computed"][f"_contract_{key}"] = value
            else:
                result[key] = value
    else:
        result["computed"]["_contract_raw"] = contract


def deep_update(target, patch):
    if not isinstance(patch, dict):
        return patch
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_update(target[key], value)
        else:
            target[key] = value
    return target


def collect_sources(task_path, case_path, contract_key, summary):
    sources = []
    task_text = read_text_lenient(task_path) if task_path else ""
    for needle in ("判断本课题是否做对", "按内容判断"):
        if needle in task_text:
            for line in task_text.splitlines():
                if needle in line:
                    sources.append({
                        "item": "判题标准", "locator": "/input/task.md",
                        "quote": line.strip(),
                    })
                    break
    if case_path.exists():
        case_obj, err, _ = load_case_and_task(case_path, None)
        if err is None and isinstance(case_obj, dict) and contract_key:
            sources.append({
                "item": "output_contract",
                "locator": f"/input/case.json",
                "quote": f"契约键 {contract_key} 由脚本自动识别并回填",
            })
    if summary:
        sources.append({
            "item": "机械盘点/去重/版本/对账统计",
            "locator": "/app/work/inventory.json",
            "quote": ("脚本按内容 SHA-256 与归一化主干计算，summary=" +
                      json.dumps(summary, ensure_ascii=False)),
        })
    return sources


def finalize(cfg) -> dict:
    """Run the full pipeline and return the result dict (side effects write
    artifacts under ``cfg.output_dir`` and ``cfg.work_dir``)."""
    t0 = time.time()
    case_path = cfg.input_dir / "case.json"
    task_path = cfg.input_dir / "task.md"

    case_obj, case_err, task_text = load_case_and_task(case_path, task_path)
    result, contract = build_skeleton(case_obj, case_path)
    if case_err or not case_path.exists():
        result["status"] = "input_error"
        result["errors"].append({
            "code": "case_json_unreadable", "path": str(case_path),
            "detail": case_err or "missing",
        })
    merge_contract(result, contract)
    if not result["case_id"] and isinstance(case_obj, dict):
        result["case_id"] = extract_case_id(case_obj, case_path) or \
            f"case-{case_path.stem}"

    # ---- phase 0: skeleton-first persistence --------------------------------
    if not cfg.skeleton_only:
        _append_log(result, "phase_0_skeleton", "写入 partial 骨架", cfg)
        atomic_write_json(cfg.output_dir / RESULT_NAME, result)
        _append_operation_log(cfg.work_dir, result["case_id"], "phase_0_skeleton",
                              "partial skeleton persisted before inventory")

    files, dirs, errors, degradations = inventory(cfg.input_dir,
                                                  cfg.max_hash_bytes)
    dirs = mark_empty_dirs(dirs, files)
    result["computed"]["files"] = files
    result["computed"]["summary"]["files"] = len(files)
    result["computed"]["summary"]["dirs"] = len(dirs)
    result["computed"]["summary"]["hashed"] = sum(
        1 for f in files if f.get("hash_status") == "ok")
    result["errors"].extend(errors)
    result["degradations"].extend(degradations)

    dup = deduplicate(files)
    ver = version_candidates(files)
    al = aliases(files)
    notes = parse_version_notes(task_path, case_path, files, cfg.input_dir)
    records, catalogs, catalog_errors = parse_catalog(files, cfg.input_dir)
    result["errors"].extend(catalog_errors)
    recon = reconcile(files, records)
    index = build_index(files, records, dup, ver)
    answers = answer_questions(task_path, case_path, files, dirs, dup, ver,
                               al, recon, records)

    computed = {
        "inventory": files,
        "duplicate_groups": dup,
        "version_candidates": ver,
        "version_notes": notes,
        "aliases": al,
        "reconciliation": recon,
        "catalogs": catalogs,
        "index": index,
        "answers": answers,
    }
    result["computed"].update(computed)
    result["computed"]["summary"].update({
        "duplicate_groups": len(dup),
        "version_candidates": len(ver),
        "symlinks": len(al["symlinks"]),
        "broken_symlinks": sum(1 for s in al["symlinks"]
                               if s["status"] == "broken"),
        "hardlink_groups": len(al["hardlink_groups"]),
        "reconcile_mismatches": recon["summary"]["mismatches"],
        "reconcile_missing": recon["summary"]["missing"],
        "reconcile_orphaned": recon["summary"]["orphaned"],
    })

    # ---- artifacts -----------------------------------------------------------
    out = cfg.output_dir
    work = cfg.work_dir
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    manifest = [{
        "path": f["path"], "type": f.get("type"), "size": f.get("size"),
        "mtime": f.get("mtime"), "mode": f.get("mode"),
        "sha256": f.get("hash"), "hash_status": f.get("hash_status"),
        "target": f.get("target", ""), "link_status": f.get("link_status", ""),
    } for f in files]
    _write_manifest_csv(out / "library_manifest.csv", manifest)
    atomic_write_text(out / "directory_tree.txt", build_tree(files, dirs))
    atomic_write_json(out / "dedup_version.json", {
        "duplicate_groups": dup,
        "version_candidates": ver,
        "version_notes": notes,
        "aliases": al,
        "heuristic": True,
    })
    atomic_write_json(out / "recovery_report.json", recon)
    atomic_write_json(out / "library_index.json", {
        "index": index, "catalogs": catalogs,
    })
    atomic_write_json(work / "inventory.json", {
        "files": files, "dirs": dirs,
    })
    _write_md_report(out / "library_manifest.md", result)

    # operation_log.jsonl lives in work for continuation and is also copied to
    # output for independent review.
    op_src = work / "operation_log.jsonl"
    if op_src.exists():
        try:
            (out / "operation_log.jsonl").write_bytes(op_src.read_bytes())
        except OSError:
            pass

    artifacts = [
        {"path": "result.json", "kind": "json",
         "role": "主结果（含全部契约字段与证据）"},
        {"path": "library_manifest.csv", "kind": "csv",
         "role": "文件清单（路径/类型/大小/mtime/权限/hash）"},
        {"path": "directory_tree.txt", "kind": "text", "role": "目录树"},
        {"path": "dedup_version.json", "kind": "json",
         "role": "重复组/版本候选/版本说明/别名"},
        {"path": "recovery_report.json", "kind": "json", "role": "恢复对账"},
        {"path": "library_index.json", "kind": "json", "role": "可检索索引"},
        {"path": "library_manifest.md", "kind": "md", "role": "交接报告"},
        {"path": "operation_log.jsonl", "kind": "jsonl", "role": "操作日志"},
    ]
    result["artifacts"] = artifacts

    # ---- sources / uncertainty / status ------------------------------------
    result["sources"] = collect_sources(task_path, case_path,
                                        result["computed"].get("_contract_key"),
                                        result["computed"]["summary"])
    if ver:
        result["uncertainty"].append({
            "item": "version_candidates",
            "reason": "版本链按归一化主干+修改时间为启发式候选，需人工确认",
            "severity": "medium",
        })
    if notes:
        result["uncertainty"].append({
            "item": "version_notes",
            "reason": "行政版本说明为关键字启发式抽取，未做语义对齐",
            "severity": "low",
        })
    if any(recon["summary"][k] for k in ("mismatches", "missing", "orphaned")):
        result["uncertainty"].append({
            "item": "reconciliation",
            "reason": "库目录与盘点存在 mismatch/missing/orphaned，需核对声明来源",
            "severity": "medium",
        })
    result["pending_human"].append({
        "item": "version_alias_review",
        "detail": "确认版本候选链与别名关系后再签署交接",
    })
    result["limitations"].append(
        "内容哈希对超过上限/非常规/无权限文件降级为 metadata-only，不伪造哈希")

    if result["status"] != "input_error":
        if not files:
            result["status"] = "insufficient_evidence"
        elif result["errors"] or result["degradations"]:
            result["status"] = "partial"
        else:
            result["status"] = "ok"
    result["runtime"] = {
        "wall_seconds": round(time.time() - t0, 3),
        "request_budget_seconds": cfg.time_budget,
        "request_budget": cfg.request_budget,
        "elapsed_seconds": round(time.time() - t0, 3),
    }
    _append_log(result, "phase_1_finalize",
                "完成盘点/去重/版本/别名/对账/索引/问答并写产物", cfg)

    atomic_write_json(out / RESULT_NAME, result)
    _append_operation_log(work, result["case_id"], "phase_1_finalize",
                          f"finalize: {len(files)} files, "
                          f"{len(dup)} dup groups, {len(ver)} version "
                          f"candidates, {len(al['symlinks'])} symlinks, "
                          f"{recon['summary']['mismatches']} mismatches")
    return result


def _append_log(result, step, detail, cfg):
    result.setdefault("change_log", []).append({
        "time": utc_now(), "actor": "scripts/finalize.py",
        "step": step, "detail": detail,
    })


def _append_operation_log(work_dir, case_id, step, detail):
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        with open(work_dir / "operation_log.jsonl", "a",
                  encoding="utf-8") as fh:
            fh.write(json.dumps({
                "time": utc_now(), "case_id": case_id, "actor": "script",
                "step": step, "detail": detail,
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _write_manifest_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["path", "type", "size", "mtime", "mode", "sha256",
                         "hash_status", "target", "link_status"])
        for r in rows:
            writer.writerow([r[k] for k in (
                "path", "type", "size", "mtime", "mode", "sha256",
                "hash_status", "target", "link_status")])


def _write_md_report(path, result):
    lines = [
        "# 资料库整理交接报告",
        "",
        f"- case_id: `{result['case_id']}`",
        f"- status: `{result['status']}`",
        f"- 文件数: {result['computed']['summary'].get('files', 0)}",
        f"- 目录数: {result['computed']['summary'].get('dirs', 0)}",
        f"- 成功哈希: {result['computed']['summary'].get('hashed', 0)}",
        f"- 重复组: {result['computed']['summary'].get('duplicate_groups', 0)}",
        f"- 版本候选: {result['computed']['summary'].get('version_candidates', 0)}",
        f"- 对账缺失: {result['computed']['summary'].get('reconcile_missing', 0)}",
        "",
        "## 状态说明",
        "",
        f"{result['status']}",
        "",
    ]
    if result.get("errors"):
        lines.append("## errors")
        for e in result["errors"]:
            lines.append(f"- `{e.get('code')}` {e.get('path')}: {e.get('detail')}")
        lines.append("")
    if result.get("degradations"):
        lines.append("## degradations")
        for d in result["degradations"]:
            lines.append(f"- `{d.get('code')}` {d.get('path')}: {d.get('detail')}")
        lines.append("")
    if result.get("uncertainty"):
        lines.append("## uncertainty")
        for u in result["uncertainty"]:
            lines.append(f"- {u.get('item')}: {u.get('reason')}")
        lines.append("")
    atomic_write_text(path, "\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Deterministic document-library repair pipeline",
        add_help=True,
    )
    p.add_argument("--input-dir", dest="input_dir", default=str(DEFAULT_INPUT))
    p.add_argument("--output-dir", dest="output_dir", default=str(DEFAULT_OUTPUT))
    p.add_argument("--work-dir", dest="work_dir", default=str(DEFAULT_WORK))
    p.add_argument("--case-json", dest="case_json", default=None)
    p.add_argument("--task-path", dest="task_path", default=None)
    p.add_argument("--result", dest="result_path", default=None)
    p.add_argument("--max-hash-bytes", dest="max_hash_bytes", type=int,
                   default=DEFAULT_MAX_HASH_BYTES)
    p.add_argument("--time-budget", dest="time_budget", type=int, default=900)
    p.add_argument("--request-budget", dest="request_budget", type=int,
                   default=64)
    p.add_argument("--skeleton-only", action="store_true")
    p.add_argument("--update", dest="update", default=None, metavar="PATCH.json")
    p.add_argument("--check", action="store_true")
    p.add_argument("--selftest", action="store_true")
    return p.parse_args(argv)


def run_update(result_path: Path, patch_path: Path, case_path: Path) -> int:
    result, err = load_json_lenient(result_path)
    if err or not isinstance(result, dict):
        print(f"update: cannot read result {result_path}: {err}")
        return 1
    patch, perr = load_json_lenient(patch_path)
    if perr or not isinstance(patch, dict):
        print(f"update: cannot read patch {patch_path}: {perr}")
        return 1
    result = deep_update(result, patch)
    result.setdefault("change_log", []).append({
        "time": utc_now(), "actor": "scripts/finalize.py --update",
        "step": "phase_2_semantic", "detail": f"merged patch {patch_path.name}",
    })
    atomic_write_json(result_path, result)
    return 0


def run_check(result_path: Path, case_path: Path) -> int:
    result, err = load_json_lenient(result_path)
    problems = []
    if err or not isinstance(result, dict):
        print(f"check: FAIL result unreadable: {err}")
        return 1
    if not result.get("case_id"):
        problems.append("case_id empty")
    if result.get("status") not in STATUS_VALUES:
        problems.append("status invalid")
    case_obj, cerr = load_json_lenient(case_path)
    contract, contract_key = find_contract(case_obj, case_path)
    if contract is not None and isinstance(contract, dict):
        for key in contract:
            present = key in result or \
                f"_contract_{key}" in result.get("computed", {})
            if not present:
                problems.append(f"contract field missing: {key}")
    if not result.get("sources") and not result.get("errors"):
        problems.append("sources empty (and no error recorded)")
    if problems:
        print("check: FAIL " + "; ".join(problems))
        return 1
    print("check: OK")
    return 0


def run_selftest() -> int:
    import tempfile
    import shutil
    import platform

    tmp = Path(tempfile.mkdtemp(prefix="libskill_selftest_"))
    input_dir = tmp / "input"
    output_dir = tmp / "output"
    work_dir = tmp / "work"
    try:
        (input_dir / "materials").mkdir(parents=True)
        (input_dir / "materials" / "empty").mkdir()
        contract = {
            "library": {"count": 3},
            "owner": "selftest",
            "notes": ["deterministic"],
        }
        (input_dir / "case.json").write_text(
            json.dumps({"case_id": "self-test-001",
                        "output_contract": contract}, ensure_ascii=False),
            encoding="utf-8",
        )
        (input_dir / "task.md").write_text(
            "# task\n判断本课题是否做对，不看移动了多少文件，而看版本按内容判断\n"
            "请统计有多少个文件、重复、版本、断链。\n",
            encoding="utf-8",
        )
        (input_dir / "materials" / "report_v1.txt").write_text(
            "same content", encoding="utf-8")
        (input_dir / "materials" / "report_v2.txt").write_text(
            "same content", encoding="utf-8")
        (input_dir / "materials" / "data.csv").write_text(
            "name,value\na,1\n", encoding="utf-8")
        (input_dir / "materials" / "bad.json").write_text(
            "{bad json", encoding="utf-8")
        (input_dir / "materials" / "library.json").write_text(
            json.dumps({"files": [
                {"path": "materials/report_v1.txt",
                 "sha256": "0000000000000000000000000000000000000000000000000000000000000000"},
            ]}, ensure_ascii=False),
            encoding="utf-8",
        )
        if hasattr(os, "symlink") and platform.system() != "Windows":
            try:
                os.symlink("no-such-target",
                           input_dir / "materials" / "broken_link")
            except OSError:
                pass

        cfg = SimpleNamespace(
            input_dir=input_dir, output_dir=output_dir, work_dir=work_dir,
            case_json=input_dir / "case.json",
            task_path=input_dir / "task.md",
            max_hash_bytes=DEFAULT_MAX_HASH_BYTES, time_budget=900,
            request_budget=64, skeleton_only=False,
        )
        result = finalize(cfg)

        checks = []
        checks.append(("case_id", result.get("case_id") == "self-test-001"))
        checks.append(("status ok", result.get("status") in
                       ("ok", "partial")))
        checks.append(("contract count", result.get("library", {})
                       .get("count") == 3))
        checks.append(("contract owner", result.get("owner") == "selftest"))
        checks.append(("dup groups",
                       result["computed"]["summary"]["duplicate_groups"] == 1))
        checks.append(("version candidates",
                       result["computed"]["summary"]["version_candidates"] >= 1))
        checks.append(("bad json error",
                       any(e.get("code") == "json_parse_error"
                           for e in result["errors"])))
        checks.append(("reconcile mismatch",
                       result["computed"]["summary"]["reconcile_mismatches"] >= 1))
        for name in ("result.json", "library_manifest.csv",
                     "directory_tree.txt", "dedup_version.json",
                     "recovery_report.json", "library_index.json",
                     "library_manifest.md"):
            checks.append((f"artifact {name}",
                           (output_dir / name).exists()))
        checks.append(("operation log",
                       (work_dir / "operation_log.jsonl").exists()))

        failed = [name for name, ok in checks if not ok]
        if failed:
            print("selftest: FAIL " + ", ".join(failed))
            return 1
        print("selftest: OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class SimpleNamespace:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def main(argv=None) -> int:
    args = parse_args(argv)
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    work_dir = Path(args.work_dir)
    case_path = Path(args.case_json) if args.case_json else \
        input_dir / "case.json"
    task_path = Path(args.task_path) if args.task_path else \
        input_dir / "task.md"
    result_path = Path(args.result_path) if args.result_path else \
        output_dir / RESULT_NAME

    if args.selftest:
        return run_selftest()

    if args.check:
        return run_check(result_path, case_path)

    if args.update:
        return run_update(result_path, Path(args.update), case_path)

    if args.skeleton_only:
        case_obj, case_err, _ = load_case_and_task(case_path, task_path)
        result, contract = build_skeleton(case_obj, case_path)
        if case_err or not case_path.exists():
            result["status"] = "input_error"
            result["errors"].append({
                "code": "case_json_unreadable", "path": str(case_path),
                "detail": case_err or "missing",
            })
        merge_contract(result, contract)
        if not result["case_id"]:
            result["case_id"] = f"case-{case_path.stem}"
        output_dir.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        result["runtime"] = {"wall_seconds": 0.0}
        result.setdefault("change_log", []).append({
            "time": utc_now(), "actor": "scripts/finalize.py --skeleton-only",
            "step": "phase_0_skeleton", "detail": "partial skeleton persisted",
        })
        atomic_write_json(result_path, result)
        _append_operation_log(work_dir, result["case_id"], "phase_0_skeleton",
                              "skeleton-only partial result")
        print(f"skeleton-only: wrote {result_path} "
              f"(status={result['status']})")
        return 0

    cfg = SimpleNamespace(
        input_dir=input_dir, output_dir=output_dir, work_dir=work_dir,
        case_json=case_path, task_path=task_path,
        max_hash_bytes=args.max_hash_bytes, time_budget=args.time_budget,
        request_budget=args.request_budget, skeleton_only=False,
    )
    try:
        result = finalize(cfg)
    except OSError as exc:
        # Write-failure fallback: keep whatever skeleton is on disk, exit 2.
        print(f"finalize: write failure: {exc}", file=sys.stderr)
        return 2
    print("finalize:", json.dumps(result["computed"]["summary"],
                                  ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
