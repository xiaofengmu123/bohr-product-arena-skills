"""core_impl.py - document library repair: deterministic inventory, dedup,
version candidates, aliases, catalog reconciliation, index, answers, and a
resumable result/checkpoint pipeline.  Standard library only.

Design principles (variant: time budget + early skeleton):
  * The first write is always a legal partial result (skeleton).  Every later
    step improves it incrementally; a crash therefore never leaves an empty or
    invalid /app/output/result.json.
  * All traversal is sorted, hashing is content-based, and there is no source
    of randomness, so repeat runs agree except for the runtime timestamps.
  * The script layer does mechanical work (walk, hash, dedup, version,
    alias, catalog, index, mechanical answers).  Semantic judgement and
    evidence quotes are left to the Agent, which merges them with --update.
"""

import argparse
import csv
import difflib
import hashlib
import io
import json
import os
import re
import sqlite3
import stat as statmod
import sys
import time

try:  # prefer UTF-8 stdio; fall back silently on old interpreters
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")
CONTRACT_KEYS = ("output_contract", "output", "result_schema", "contract", "output_schema")
RESERVED_KEYS = {
    "case_id", "status", "task_id", "task_title", "output_contract",
    "sources", "uncertainty", "change_log", "errors", "degradations",
    "limitations", "pending_human", "runtime", "computed", "artifacts",
}
DEFAULT_MAX_HASH_BYTES = 2 * 1024 * 1024
PREVIEW_CHARS = 2000
INDEX_TEXT_CHARS = 20000
BINARY_EXTENSIONS = {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp",
    "zip", "gz", "bz2", "xz", "7z", "rar", "tar", "png", "jpg", "jpeg", "gif",
    "tif", "tiff", "bmp", "webp", "svgz", "mp3", "mp4", "wav", "avi", "mov",
    "db", "sqlite", "sqlite3", "pickle", "pkl", "npy", "npz", "h5", "hdf5",
    "mat", "sav", "dta", "exe", "dll", "so", "dylib",
}
INPUT_ROLE_BASENAMES = {"case.json", "task.md"}
CATALOG_PATH_KEYS = ("path", "file", "filename", "filepath", "relpath", "relative_path", "name")

PLAN_PHASES = [
    {"id": "0_skeleton", "name": "骨架早落盘", "budget_s": 180, "request_budget": 6,
     "goal": "run --skeleton-only; write legal partial result.json; read task.md rules"},
    {"id": "1_mechanical", "name": "盘点与机械产物", "budget_s": 120, "request_budget": 0,
     "goal": "run full pipeline once; inventory/hash/dedup/version/alias/catalog/index/answers"},
    {"id": "2_semantic", "name": "语义归位与增量", "budget_s": 360, "request_budget": 40,
     "goal": "inspect materials; fix org/dedup/version/metadata/answers via --update patches"},
    {"id": "3_evidence", "name": "证据与复核", "budget_s": 120, "request_budget": 12,
     "goal": "add sources/uncertainty; run --check; resolve conflicts"},
    {"id": "4_finalize", "name": "定稿", "budget_s": 120, "request_budget": 6,
     "goal": "set honest status; confirm contract fields; final --check"},
]
TOTAL_BUDGET_S = 900
TOTAL_REQUEST_BUDGET = 64


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time()))


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def read_text_file(path, max_bytes=None):
    """Read text with BOM tolerance and encoding fallback. Returns (text, encoding, ok)."""
    with open(path, "rb") as fh:
        data = fh.read() if max_bytes is None else fh.read(max_bytes)
    return decode_bytes(data)


def decode_bytes(data):
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(enc), enc, True
        except UnicodeDecodeError:
            continue
    if b"\x00" in data[:8192]:
        return None, "binary", False
    try:
        return data.decode("latin-1"), "latin-1", False
    except Exception:
        return None, "binary", False


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return default


def write_json(path, obj):
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def append_jsonl(path, obj):
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def relpath_slash(path, root):
    rel = os.path.relpath(path, root)
    return rel.replace(os.sep, "/")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def load_patch(path):
    if path is None or not os.path.exists(path):
        return {}
    return read_json(path, {})


# ---------------------------------------------------------------------------
# Input contract discovery
# ---------------------------------------------------------------------------

def discover_case_id(case, task_text):
    if isinstance(case, dict):
        for k in ("case_id", "id", "challenge_id", "challengeId", "armiId", "task_id"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if task_text:
        m = re.search(r"challenge[-_]?id[:：]\s*`?([A-Za-z0-9._-]+)`?", task_text)
        if m:
            return m.group(1)
        m = re.search(r"challenge[-_]?id[:：]\s*([A-Za-z0-9._-]+)", task_text)
        if m:
            return m.group(1)
    return "unknown"


def discover_contract(case):
    """Return (key, contract_dict_or_None)."""
    if isinstance(case, dict):
        for k in CONTRACT_KEYS:
            if k in case:
                v = case[k]
                if isinstance(v, dict):
                    return k, v
                return k, {"_raw": v}
    return None, None


def default_contract():
    return {
        "library_summary": None,
        "file_manifest": [],
        "directory_tree": None,
        "metadata": [],
        "version_relations": [],
        "reconciliation": None,
        "index": None,
        "answers": [],
        "hashes": [],
        "permissions": [],
        "changes": [],
        "skipped": [],
        "conflicts": [],
        "recovery": [],
    }


def task_title(case, task_text):
    if isinstance(case, dict):
        for k in ("title", "name"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if task_text:
        for line in task_text.splitlines():
            s = line.strip()
            if s.startswith("#") and not s.startswith("##"):
                return s.lstrip("#").strip()
    return None


# ---------------------------------------------------------------------------
# Result skeleton
# ---------------------------------------------------------------------------

def build_skeleton(case_id, status, contract, title, task_id=None):
    result = {
        "case_id": case_id,
        "status": status,
        "task_id": task_id or case_id,
        "task_title": title,
        "output_contract": contract if isinstance(contract, dict) else None,
        "sources": [],
        "uncertainty": [],
        "change_log": [
            {"time": now_utc(), "actor": "script", "step": "skeleton",
             "detail": "legal partial result written before analysis"}
        ],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "pending_human": [],
        "runtime": {"started_utc": now_utc(), "elapsed_s": 0.0, "tool": "python-stdlib"},
        "computed": {},
        "artifacts": {},
    }
    _fill_contract_placeholders(result, contract)
    return result


def _fill_contract_placeholders(result, contract):
    if not isinstance(contract, dict):
        return
    for k in contract.keys():
        if k in RESERVED_KEYS:
            result.setdefault("computed", {}).setdefault("_contract_" + k, None)
            continue
        result.setdefault(k, None)


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def _file_role(rel):
    base = os.path.basename(rel)
    return "input_contract" if base in INPUT_ROLE_BASENAMES else "material"


def inventory(input_root, max_hash_bytes=DEFAULT_MAX_HASH_BYTES):
    files, dirs, symlinks, errors, degradations = [], [], [], [], []
    hardlink_map = {}
    if not os.path.isdir(input_root):
        errors.append({"path": "<input-root>", "kind": "input_root_missing",
                       "detail": "input root does not exist or is not a directory"})
        return _inventory_result(files, dirs, [], [], errors, degradations, [])

    for dirpath, dirnames, filenames in os.walk(input_root, topdown=True):
        dirnames.sort()
        filenames.sort()
        rel_dir = relpath_slash(dirpath, input_root)
        dirs.append({"path": rel_dir, "role": "directory"})
        for fn in filenames:
            abs_path = os.path.join(dirpath, fn)
            rel = relpath_slash(abs_path, input_root)
            try:
                st = os.lstat(abs_path)
            except OSError as exc:
                errors.append({"path": rel, "kind": "lstat_error", "detail": str(exc)})
                continue
            entry = {
                "path": rel, "role": _file_role(rel), "size": st.st_size,
                "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
                "mode": oct(st.st_mode & 0o7777), "nlink": st.st_nlink,
            }
            if statmod.S_ISLNK(st.st_mode):
                entry["type"] = "symlink"
                try:
                    target = os.readlink(abs_path)
                except OSError as exc:
                    target = None
                    errors.append({"path": rel, "kind": "readlink_error", "detail": str(exc)})
                entry["link_target"] = target
                if target is not None:
                    target_abs = os.path.normpath(os.path.join(dirpath, target))
                    entry["broken"] = not os.path.exists(target_abs)
                symlinks.append(entry)
                files.append(entry)
                continue
            if not statmod.S_ISREG(st.st_mode):
                entry["type"] = "non_regular"
                entry["mode_desc"] = _mode_desc(st.st_mode)
                files.append(entry)
                degradations.append({"path": rel, "kind": "non_regular",
                                     "detail": _mode_desc(st.st_mode)})
                continue
            entry["type"] = "file"
            entry["extension"] = (os.path.splitext(fn)[1].lstrip(".").lower() or None)
            entry["readable"] = os.access(abs_path, os.R_OK)
            if st.st_nlink > 1:
                key = (st.st_dev, st.st_ino)
                hardlink_map.setdefault(key, []).append(rel)
                entry["inode"] = st.st_ino
            if not entry["readable"]:
                entry["hash"] = None
                entry["hash_status"] = "unreadable"
                files.append(entry)
                degradations.append({"path": rel, "kind": "unreadable", "detail": "no read access"})
                continue
            if st.st_size > max_hash_bytes:
                entry["hash"] = None
                entry["hash_status"] = "too_large"
                files.append(entry)
                degradations.append({"path": rel, "kind": "too_large",
                                     "detail": "size %d > max_hash %d" % (st.st_size, max_hash_bytes)})
                continue
            try:
                with open(abs_path, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                entry["hash"] = None
                entry["hash_status"] = "read_error"
                files.append(entry)
                errors.append({"path": rel, "kind": "read_error", "detail": str(exc)})
                continue
            entry["sha256"] = sha256_bytes(data)
            entry["hash_status"] = "ok"
            text, encoding, strict_ok = decode_bytes(data)
            entry["encoding"] = encoding
            is_binary = entry["extension"] in BINARY_EXTENSIONS or (text is None)
            entry["is_text"] = bool(not is_binary and text is not None)
            if entry["is_text"]:
                entry["preview"] = text[:PREVIEW_CHARS]
                entry["full_text"] = text[:INDEX_TEXT_CHARS]
                if not strict_ok:
                    degradations.append({"path": rel, "kind": "encoding_fallback",
                                         "detail": "decoded with %s" % encoding})
            else:
                entry["preview"] = None
            files.append(entry)

    empty_dirs = _empty_dirs(input_root, files)
    hardlinks = [{"inode": key[1], "paths": sorted(paths)}
                 for key, paths in sorted(hardlink_map.items(), key=lambda kv: kv[0][1])
                 if len(paths) > 1]
    return _inventory_result(files, dirs, empty_dirs, symlinks, errors, degradations, hardlinks)


def _mode_desc(mode):
    if statmod.S_ISDIR(mode):
        return "directory"
    if statmod.S_ISLNK(mode):
        return "symlink"
    if statmod.S_ISREG(mode):
        return "regular"
    if statmod.S_ISFIFO(mode):
        return "fifo"
    if statmod.S_ISSOCK(mode):
        return "socket"
    if statmod.S_ISCHR(mode):
        return "char_device"
    if statmod.S_ISBLK(mode):
        return "block_device"
    return "other"


def _empty_dirs(input_root, files):
    """A directory is empty when no inventoried file lives beneath it."""
    result = []
    for dirpath, dirnames, filenames in os.walk(input_root, topdown=True):
        dirnames.sort()
        filenames.sort()
        rel = relpath_slash(dirpath, input_root)
        if rel in ("", "."):
            continue
        prefix = rel + "/"
        if not any(f["path"] == rel or f["path"].startswith(prefix) for f in files):
            result.append(rel)
    result.sort()
    return result


def _inventory_result(files, dirs, empty_dirs, symlinks, errors, degradations, hardlinks):
    files.sort(key=lambda x: x["path"])
    dirs.sort(key=lambda x: x["path"])
    return {
        "files": files, "dirs": dirs, "empty_dirs": empty_dirs,
        "symlinks": symlinks, "errors": errors, "degradations": degradations,
        "hardlink_groups": hardlinks,
    }


# ---------------------------------------------------------------------------
# Dedup, version, aliases
# ---------------------------------------------------------------------------

def _norm_stem(path):
    base = os.path.basename(path)
    stem = os.path.splitext(base)[0].lower()
    stem = re.sub(r"[\s\-_.,;()\[\]{}]+", "_", stem)
    stem = re.sub(r"_(final|draft|rev|revision|copy|副本|old|new|updated|clean|scan|scanned|"
                  r"submitted|submission|accepted|published|edited|ed|v\d+(\.\d+)*|\d+(\.\d+)*)$", "", stem)
    stem = re.sub(r"_\d{4}[-_]\d{2}[-_]\d{2}$", "", stem)
    stem = re.sub(r"_\d{8}$", "", stem)
    stem = re.sub(r"_+", "_", stem).strip("_")
    return stem or os.path.splitext(base)[0].lower()


def dedup_and_versions(inv):
    hashed = [f for f in inv["files"]
              if f.get("type") == "file" and f.get("hash_status") == "ok"]
    by_hash = {}
    for f in hashed:
        by_hash.setdefault(f["sha256"], []).append(f["path"])
    dup_groups = [{"sha256": h, "paths": sorted(p), "count": len(p)}
                  for h, p in sorted(by_hash.items()) if len(p) > 1]

    by_stem = {}
    for f in hashed:
        by_stem.setdefault(_norm_stem(f["path"]), []).append(f)
    version_candidates = []
    version_notes = []
    for stem in sorted(by_stem):
        group = sorted(by_stem[stem], key=lambda x: x["path"])
        if len(group) < 2:
            continue
        ids = [g["path"] for g in group]
        unique_hashes = {g["sha256"] for g in group}
        sims = []
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a = group[i].get("preview") or ""
                b = group[j].get("preview") or ""
                ratio = difflib.SequenceMatcher(None, a, b).ratio() if (a or b) else 0.0
                sims.append({"a": group[i]["path"], "b": group[j]["path"],
                             "similarity": round(ratio, 4)})
        version_candidates.append({
            "normalized_stem": stem, "paths": ids, "distinct_hashes": len(unique_hashes),
            "pairwise_similarity": sorted(sims, key=lambda s: (-s["similarity"], s["a"], s["b"])),
        })
        version_notes.append({"stem": stem,
                              "note": "heuristic: same normalized stem, %d content hash(es)" % len(unique_hashes)})
    return {
        "duplicate_groups": dup_groups,
        "version_candidates": version_candidates,
        "version_notes": version_notes,
        "aliases": {
            "symlinks": [s for s in inv["symlinks"]],
            "hardlink_groups": inv["hardlink_groups"],
            "broken_symlinks": [s for s in inv["symlinks"] if s.get("broken")],
        },
    }


# ---------------------------------------------------------------------------
# Catalog parsing and reconciliation
# ---------------------------------------------------------------------------

def parse_catalog(entry):
    """Best-effort parse of a library catalog file. Returns records or None."""
    if entry.get("type") != "file" or entry.get("hash_status") != "ok":
        return None
    ext = entry.get("extension")
    path = entry["path"]
    text = entry.get("full_text") or entry.get("preview") or ""
    if ext == "json":
        try:
            obj = json.loads(text)
        except Exception:
            return None
        if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
            return {"kind": "json", "source": path, "records": obj}
        if isinstance(obj, dict):
            for v in obj.values():
                if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                    return {"kind": "json", "source": path, "records": v}
        return None
    if ext == "csv":
        try:
            rows = list(csv.DictReader(io.StringIO(text)))
        except Exception:
            return None
        if rows:
            return {"kind": "csv", "source": path, "records": rows}
        return None
    if ext in ("md", "markdown"):
        rows = _parse_markdown_tables(text)
        if rows:
            return {"kind": "markdown", "source": path, "records": rows}
        return None
    if ext in ("bib", "bibtex"):
        recs = _parse_bibtex(text)
        if recs:
            return {"kind": "bibtex", "source": path, "records": recs}
        return None
    if ext in ("ris",):
        recs = _parse_ris(text)
        if recs:
            return {"kind": "ris", "source": path, "records": recs}
        return None
    if ext in ("db", "sqlite", "sqlite3"):
        return _parse_sqlite(path)
    # generic text: look for a markdown table even without the extension
    rows = _parse_markdown_tables(text)
    if rows and len(rows) >= 1:
        return {"kind": "markdown", "source": path, "records": rows}
    return None


def _parse_markdown_tables(text):
    records = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "|" in line:
            block = []
            while i < len(lines) and "|" in lines[i]:
                block.append(lines[i].strip())
                i += 1
            if len(block) >= 2:
                header = [c.strip() for c in block[0].strip("|").split("|")]
                sep = block[1]
                if set(sep.replace("|", "").replace("-", "").replace(":", "").strip()) == set():
                    for row in block[2:]:
                        cells = [c.strip() for c in row.strip("|").split("|")]
                        if len(cells) == len(header):
                            records.append(dict(zip(header, cells)))
            continue
        i += 1
    return records


def _parse_bibtex(text):
    records = []
    pattern = re.compile(r"@\w+\s*\{\s*([^,]+),\s*(.*?)\}\s*(?=@|\Z)", re.S | re.I)
    for m in pattern.finditer(text):
        key = m.group(1).strip()
        body = m.group(2)
        rec = {"_key": key}
        field_pat = re.compile(r"(\w+)\s*=\s*[{\"](.*?)[}\"]", re.S)
        for fm in field_pat.finditer(body):
            rec[fm.group(1).lower()] = fm.group(2).strip()
        records.append(rec)
    return records


def _parse_ris(text):
    records = []
    cur = {}
    for line in text.splitlines():
        if len(line) >= 6 and line[2:4] == "  ":
            tag = line[:2].upper()
            val = line[6:].strip()
            if tag == "ER" and cur:
                records.append(cur)
                cur = {}
            else:
                cur[tag.lower()] = val
        elif line.strip() == "" and cur:
            records.append(cur)
            cur = {}
    if cur:
        records.append(cur)
    return records


def _parse_sqlite(path):
    try:
        con = sqlite3.connect(path)
    except Exception:
        return None
    try:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        records = []
        for t in tables:
            try:
                cur.execute("SELECT * FROM %s LIMIT 500" % _quote_ident(t))
                cols = [d[0] for d in cur.description or []]
                for row in cur.fetchall():
                    records.append(dict(zip(cols, row)))
            except Exception:
                continue
        con.close()
        return {"kind": "sqlite", "source": path, "records": records,
                "tables": tables}
    except Exception:
        try:
            con.close()
        except Exception:
            pass
        return None


def _quote_ident(name):
    return '"' + name.replace('"', '""') + '"'


def _catalog_paths(records):
    paths = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        for k in CATALOG_PATH_KEYS:
            v = rec.get(k)
            if isinstance(v, str) and v.strip():
                paths.append({"path": v.strip(), "record": rec})
                break
    return paths


def reconcile(inv, catalogs):
    disk_paths = {f["path"] for f in inv["files"]}
    disk_by_path = {f["path"]: f for f in inv["files"]}
    if not catalogs:
        return {"not_applicable": True, "detail": "no catalog file detected",
                "matches": [], "mismatches": [], "missing": [], "orphaned": []}
    matches, mismatches, missing = [], [], []
    seen = set()
    for cat in catalogs:
        for item in _catalog_paths(cat.get("records", [])):
            p = item["path"].replace("\\", "/").lstrip("./")
            if p in seen:
                continue
            seen.add(p)
            if p in disk_paths:
                entry = disk_by_path[p]
                rec = item["record"]
                ok = True
                detail = {}
                if isinstance(rec.get("sha256"), str):
                    if rec["sha256"].lower() != entry.get("sha256"):
                        ok = False
                        detail["hash"] = "mismatch"
                if isinstance(rec.get("size"), (int, float)) and not isinstance(rec.get("size"), bool):
                    if int(rec["size"]) != entry.get("size"):
                        ok = False
                        detail["size"] = "mismatch"
                (matches if ok else mismatches).append(
                    {"catalog_path": p, "disk_path": p, "source": cat["source"], "detail": detail})
            else:
                missing.append({"catalog_path": p, "source": cat["source"],
                                "record": item["record"]})
    orphaned = [f["path"] for f in inv["files"]
                if f["path"] not in seen and f["role"] == "material"]
    return {"not_applicable": False,
            "catalog_sources": [c["source"] for c in catalogs],
            "matches": matches, "mismatches": mismatches,
            "missing": missing, "orphaned": orphaned}


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

def build_index(inv, dedup_version):
    entries = []
    for f in inv["files"]:
        e = {
            "path": f["path"], "role": f["role"], "type": f["type"],
            "size": f.get("size"), "extension": f.get("extension"),
            "sha256": f.get("sha256"), "encoding": f.get("encoding"),
            "normalized_stem": _norm_stem(f["path"]) if f.get("type") == "file" else None,
            "keywords": _keywords(f["path"]),
        }
        text = f.get("full_text") or f.get("preview")
        if text:
            e["text"] = text
        entries.append(e)
    by_hash = {}
    for g in dedup_version["duplicate_groups"]:
        by_hash[g["sha256"]] = g["paths"]
    return {"entries": entries, "by_hash": by_hash,
            "broken_symlinks": [s["path"] for s in dedup_version["aliases"]["broken_symlinks"]]}


def _keywords(path):
    base = os.path.basename(path)
    stem = os.path.splitext(base)[0]
    tokens = re.split(r"[\s\-_.,;()\[\]{}+]+", stem.lower())
    return [t for t in tokens if t]


def search_index(index, query):
    q = (query or "").strip().lower()
    if not q:
        return []
    hits = []
    for e in index["entries"]:
        hay = " ".join([e["path"].lower()] + e.get("keywords", []))
        if e.get("text"):
            hay += " " + e["text"].lower()
        if q in hay:
            hits.append(e["path"])
    return sorted(hits)


# ---------------------------------------------------------------------------
# Mechanical answers
# ---------------------------------------------------------------------------

def mechanical_answers(inv, dedup, rec, index):
    answers = []
    files = [f for f in inv["files"] if f.get("type") == "file"]
    answers.append(_a("文件总数", "inventory", len(files), "input inventory"))
    answers.append(_a("目录总数", "inventory", len(inv["dirs"]), "input inventory"))
    answers.append(_a("空目录", "inventory", len(inv["empty_dirs"]), "input inventory"))
    answers.append(_a("符号链接总数", "aliases", len(inv["symlinks"]), "aliases"))
    answers.append(_a("断链符号链接", "aliases", len(dedup["aliases"]["broken_symlinks"]),
                      "aliases.broken_symlinks"))
    answers.append(_a("硬链接组", "aliases", len(inv["hardlink_groups"]), "aliases.hardlink_groups"))
    answers.append(_a("重复组(内容SHA-256)", "dedup", len(dedup["duplicate_groups"]),
                      "dedup.duplicate_groups"))
    answers.append(_a("版本候选组(启发式)", "dedup", len(dedup["version_candidates"]),
                      "dedup.version_candidates"))
    if not rec.get("not_applicable", True):
        answers.append(_a("目录匹配", "reconciliation", len(rec.get("matches", [])),
                          "reconciliation.matches"))
        answers.append(_a("目录不一致", "reconciliation", len(rec.get("mismatches", [])),
                          "reconciliation.mismatches"))
        answers.append(_a("目录缺失", "reconciliation", len(rec.get("missing", [])),
                          "reconciliation.missing"))
        answers.append(_a("未登记(orphaned)", "reconciliation", len(rec.get("orphaned", [])),
                          "reconciliation.orphaned"))
    for f in files:
        if f.get("sha256"):
            answers.append(_a("sha256:" + f["path"], "hash", f["sha256"], "inventory.files"))
    return answers


def _a(question, category, value, locator):
    return {"question": question, "category": category, "answer": value, "locator": locator}


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

def write_artifacts(output_dir, inv, dedup, rec, index, answers):
    ensure_dir(output_dir)
    artifacts = {}

    # manifest CSV
    manifest_path = os.path.join(output_dir, "library_manifest.csv")
    cols = ["path", "role", "type", "size", "mtime", "mode", "nlink", "extension",
            "encoding", "sha256", "hash_status", "link_target", "broken", "readable"]
    with open(manifest_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for f in inv["files"]:
            w.writerow([f.get(c) if f.get(c) is not None else "" for c in cols])
    artifacts["library_manifest.csv"] = manifest_path

    # directory tree
    tree_path = os.path.join(output_dir, "directory_tree.txt")
    with open(tree_path, "w", encoding="utf-8") as fh:
        fh.write(build_tree_text_from_inv(inv))
    artifacts["directory_tree.txt"] = tree_path

    # dedup + version + aliases
    dv_path = os.path.join(output_dir, "dedup_version.json")
    write_json(dv_path, dedup)
    artifacts["dedup_version.json"] = dv_path

    # recovery report
    rec_path = os.path.join(output_dir, "recovery_report.json")
    write_json(rec_path, rec)
    artifacts["recovery_report.json"] = rec_path

    # index
    idx_path = os.path.join(output_dir, "library_index.json")
    write_json(idx_path, index)
    artifacts["library_index.json"] = idx_path

    # markdown handoff
    md_path = os.path.join(output_dir, "library_manifest.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_handoff(inv, dedup, rec, answers))
    artifacts["library_manifest.md"] = md_path

    # operation log (append)
    log_path = os.path.join(output_dir, "operation_log.jsonl")
    append_jsonl(log_path, {"time": now_utc(), "actor": "script", "step": "pipeline",
                            "detail": "inventory/dedup/version/alias/catalog/index/answers written"})
    artifacts["operation_log.jsonl"] = log_path

    return artifacts


def build_tree_text_from_inv(inv):
    lines = ["input"]
    # group children by parent
    children = {}
    for d in inv["dirs"]:
        p = d["path"]
        parent = os.path.dirname(p) if os.path.dirname(p) else ""
        children.setdefault(parent, []).append((os.path.basename(p), "d", p))
    for f in inv["files"]:
        p = f["path"]
        parent = os.path.dirname(p)
        children.setdefault(parent, []).append((os.path.basename(p), "f", p))
    for k in children:
        children[k].sort(key=lambda x: (x[1], x[0]))

    def rec(rel, prefix, is_last):
        name = os.path.basename(rel) if rel else "input"
        lines.append(prefix + ("`-- " if is_last else "|-- ") + name)
        kids = children.get(rel, [])
        for i, (cname, ctype, cpath) in enumerate(kids):
            rec(cpath, prefix + ("    " if is_last else "|   "), i == len(kids) - 1)

    kids = children.get("", [])
    for i, (cname, ctype, cpath) in enumerate(kids):
        rec(cpath, "", i == len(kids) - 1)
    for d in inv["empty_dirs"]:
        lines.append("[空目录] " + d)
    return "\n".join(lines) + "\n"


def render_handoff(inv, dedup, rec, answers):
    n_files = len([f for f in inv["files"] if f.get("type") == "file"])
    lines = [
        "# 资料库交接报告（自动生成）",
        "",
        "- 生成时间：%s" % now_utc(),
        "- 文件数：%d；目录数：%d；空目录：%d" % (n_files, len(inv["dirs"]), len(inv["empty_dirs"])),
        "- 重复组：%d；版本候选：%d" % (len(dedup["duplicate_groups"]), len(dedup["version_candidates"])),
        "- 断链符号链接：%d；硬链接组：%d" % (len(dedup["aliases"]["broken_symlinks"]),
                                             len(inv["hardlink_groups"])),
        "",
        "## 重复组",
    ]
    for g in dedup["duplicate_groups"]:
        lines.append("- %s（%d 份）：%s" % (g["sha256"][:12], g["count"], ", ".join(g["paths"])))
    lines += ["", "## 版本候选（启发式）"]
    for v in dedup["version_candidates"]:
        lines.append("- 主干 `%s`：%s" % (v["normalized_stem"], ", ".join(v["paths"])))
    lines += ["", "## 对账"]
    if rec.get("not_applicable", True):
        lines.append("- 未检测到库目录文件：" + rec.get("detail", ""))
    else:
        lines.append("- matches %d；mismatches %d；missing %d；orphaned %d" % (
            len(rec.get("matches", [])), len(rec.get("mismatches", [])),
            len(rec.get("missing", [])), len(rec.get("orphaned", []))))
    lines += ["", "## 机械问答"]
    for a in answers:
        lines.append("- %s → %s" % (a["question"], json.dumps(a["answer"], ensure_ascii=False)))
    lines += ["", "> 本报告为机械汇总；语义结论与证据见 result.json 的 sources/uncertainty。"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Budget / checkpoint ledger
# ---------------------------------------------------------------------------

def load_budget(work_dir):
    path = os.path.join(work_dir, "budget.json")
    b = read_json(path)
    if isinstance(b, dict) and "plan" in b:
        return path, b
    started = now_utc()
    b = {
        "plan": {"total_s": TOTAL_BUDGET_S, "total_requests": TOTAL_REQUEST_BUDGET,
                 "phases": PLAN_PHASES},
        "started_utc": started,
        "deadline_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                      time.gmtime(time.time() + TOTAL_BUDGET_S)),
        "checkpoints": [],
    }
    write_json(path, b)
    return path, b


def append_checkpoint(work_dir, phase_id, detail, elapsed_s=None):
    path, b = load_budget(work_dir)
    b.setdefault("checkpoints", []).append({
        "utc": now_utc(), "phase": phase_id, "detail": detail,
        "elapsed_s": round(elapsed_s if elapsed_s is not None else time.time() - _parse_utc(b.get("started_utc")), 1),
    })
    write_json(path, b)
    return path


def _parse_utc(s):
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return time.time()


def budget_report(work_dir):
    path, b = load_budget(work_dir)
    out = []
    out.append("budget plan: %d s / %d requests" % (b["plan"]["total_s"], b["plan"]["total_requests"]))
    for p in b["plan"]["phases"]:
        out.append("- %-12s %-14s %4ds / %2d requests" % (p["id"], p["name"], p["budget_s"], p["request_budget"]))
    out.append("checkpoints: %d" % len(b.get("checkpoints", [])))
    for c in b.get("checkpoints", []):
        out.append("  %s [%s] %s" % (c["utc"], c["phase"], c["detail"]))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------

def summarize(inv, dedup, rec, index, answers):
    return {
        "files": len([f for f in inv["files"] if f.get("type") == "file"]),
        "directories": len(inv["dirs"]),
        "empty_directories": len(inv["empty_dirs"]),
        "symlinks": len(inv["symlinks"]),
        "broken_symlinks": len(dedup["aliases"]["broken_symlinks"]),
        "hardlink_groups": len(inv["hardlink_groups"]),
        "duplicate_groups": len(dedup["duplicate_groups"]),
        "version_candidates": len(dedup["version_candidates"]),
        "total_bytes": sum(f.get("size") or 0 for f in inv["files"]),
        "catalog_sources": rec.get("catalog_sources", []),
        "errors": len(inv["errors"]),
        "degradations": len(inv["degradations"]),
    }


def assemble_result(case, task_text, inv, dedup, rec, index, answers, artifacts, start_t):
    contract_key, contract = discover_contract(case)
    if contract is None:
        contract = default_contract()
        contract_key = "default"
    case_id = discover_case_id(case, task_text)
    title = task_title(case, task_text)
    status = _initial_status(case, inv)
    result = build_skeleton(case_id, status, contract, title)
    result["output_contract"] = contract
    result["library_summary"] = summarize(inv, dedup, rec, index, answers)
    result["file_manifest"] = [
        {k: f.get(k) for k in ("path", "role", "type", "size", "mtime", "mode", "extension",
                               "encoding", "sha256", "hash_status", "link_target", "broken")}
        for f in inv["files"]
    ]
    result["metadata"] = [_lean_file(f) for f in inv["files"]
                          if f.get("preview") or f.get("extension")]
    result["directory_tree"] = build_tree_text_from_inv(inv)
    result["version_relations"] = dedup["version_candidates"]
    result["reconciliation"] = rec
    result["index"] = index
    result["answers"] = answers
    result["hashes"] = [{"path": f["path"], "sha256": f["sha256"]}
                        for f in inv["files"] if f.get("sha256")]
    result["permissions"] = [{"path": f["path"], "mode": f.get("mode"), "readable": f.get("readable")}
                             for f in inv["files"]]
    result["changes"] = []
    result["skipped"] = inv["degradations"]
    result["conflicts"] = rec.get("mismatches", [])
    result["recovery"] = {"missing": rec.get("missing", []), "broken_symlinks":
                          dedup["aliases"]["broken_symlinks"]}
    result["computed"] = {
        "files": [_lean_file(f) for f in inv["files"]],
        "summary": result["library_summary"],
        "duplicate_groups": dedup["duplicate_groups"],
        "version_candidates": dedup["version_candidates"],
        "version_notes": dedup["version_notes"],
        "aliases": dedup["aliases"],
        "reconciliation": rec,
        "answers": answers,
        "index": index,
        "artifacts": {k: os.path.basename(v) for k, v in artifacts.items()},
    }
    result["errors"] = inv["errors"]
    result["degradations"] = inv["degradations"]
    result["limitations"] = [
        "机械层不解析 PDF/Office 文本层；由 Agent 调用平台 pdftotext/pdf-render/ocr 工具补语义",
        "版本候选与目录对账为启发式，须以 sources/uncertainty 标注",
    ]
    result["artifacts"] = {k: os.path.basename(v) for k, v in artifacts.items()}
    result["runtime"]["elapsed_s"] = round(time.time() - start_t, 3)
    result["runtime"]["finished_utc"] = now_utc()
    result["change_log"].append({"time": now_utc(), "actor": "script", "step": "pipeline",
                                 "detail": "mechanical artifacts written"})
    return result, contract_key


def _lean_file(f):
    """Drop bulk text from result.json copies; keep it only in library_index.json."""
    out = {k: v for k, v in f.items() if k != "full_text"}
    return out


def _initial_status(case, inv):
    if not isinstance(case, dict):
        return "input_error"
    material = [f for f in inv["files"] if f.get("role") == "material"]
    if not material:
        return "insufficient_evidence"
    return "partial"


# ---------------------------------------------------------------------------
# Update / check
# ---------------------------------------------------------------------------

def deep_merge(base, patch):
    append_keys = {"change_log", "sources", "uncertainty", "errors", "degradations", "limitations"}
    for k, v in patch.items():
        if k in append_keys and isinstance(v, list) and isinstance(base.get(k), list):
            base[k].extend(v)
        elif k == "computed" and isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge_dict(base["computed"], v)
        elif isinstance(v, dict) and isinstance(base.get(k), dict) and k != "output_contract":
            _merge_dict(base[k], v)
        else:
            base[k] = v
    return base


def _merge_dict(base, patch):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge_dict(base[k], v)
        else:
            base[k] = v


def apply_update(result_path, patch_path, output_dir):
    result = read_json(result_path, {})
    patch = load_patch(patch_path)
    if not patch:
        return result, 0, "empty patch; nothing merged"
    base_contract = result.get("output_contract") if isinstance(result, dict) else None
    result = deep_merge(result or {}, patch)
    if isinstance(base_contract, dict):
        result["output_contract"] = base_contract
    write_json(result_path, result)
    append_jsonl(os.path.join(output_dir, "operation_log.jsonl"),
                 {"time": now_utc(), "actor": "script", "step": "update",
                  "detail": "patch merged from %s" % os.path.basename(patch_path)})
    return result, 0, "merged patch"


def check_result(result_path, case):
    problems = []
    result = read_json(result_path)
    if not isinstance(result, dict):
        return ["result.json is not a JSON object"], False
    if not result.get("case_id"):
        problems.append("case_id missing/empty")
    if result.get("status") not in ALLOWED_STATUS:
        problems.append("status invalid: %r" % result.get("status"))
    for k in ("sources", "uncertainty", "change_log", "errors", "degradations"):
        if k not in result:
            problems.append("missing field: %s" % k)
    contract = result.get("output_contract")
    if isinstance(contract, dict):
        for k in contract.keys():
            if k not in result and k not in RESERVED_KEYS:
                problems.append("contract field not echoed: %s" % k)
    # evidence shape check
    for s in result.get("sources", []):
        if not isinstance(s, dict) or not s.get("locator") or not s.get("quote"):
            problems.append("source entry missing locator/quote: %s" % str(s)[:80])
    return problems, not problems


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(args):
    start_t = time.time()
    case = read_json(args.case_json) if os.path.exists(args.case_json) else None
    task_text = ""
    if os.path.exists(args.task_path):
        text, _, _ = read_text_file(args.task_path)
        task_text = text or ""

    if args.skeleton_only:
        contract_key, contract = discover_contract(case)
        if contract is None:
            contract = default_contract()
        case_id = discover_case_id(case, task_text)
        title = task_title(case, task_text)
        result = build_skeleton(case_id, "input_error" if not isinstance(case, dict) else "partial",
                                contract, title)
        ensure_dir(args.output_dir)
        result_path = os.path.join(args.output_dir, "result.json")
        write_json(result_path, result)
        ensure_dir(args.work_dir)
        append_checkpoint(args.work_dir, "0_skeleton", "skeleton written to %s" % result_path,
                          time.time() - start_t)
        print("skeleton: %s (status=%s, contract=%s)" % (result_path, result["status"], contract_key))
        return 0

    if args.budget:
        print(budget_report(args.work_dir))
        return 0

    result_path = args.result or args.result_arg or os.path.join(args.output_dir, "result.json")

    if args.update:
        _, code, msg = apply_update(result_path, args.update, args.output_dir)
        print(msg)
        return code

    if args.check:
        problems, ok = check_result(result_path, case)
        if ok:
            print("check: PASS")
            return 0
        for p in problems:
            print("check: FAIL - %s" % p)
        return 1

    # full pipeline
    ensure_dir(args.output_dir)
    ensure_dir(args.work_dir)
    try:
        inv = inventory(args.input_root, args.max_hash)
        dedup = dedup_and_versions(inv)
        catalogs = []
        for f in inv["files"]:
            if f.get("role") == "material":
                c = parse_catalog(f)
                if c:
                    catalogs.append(c)
        rec = reconcile(inv, catalogs)
        index = build_index(inv, dedup)
        answers = mechanical_answers(inv, dedup, rec, index)
        artifacts = write_artifacts(args.output_dir, inv, dedup, rec, index, answers)
        result, _ = assemble_result(case, task_text, inv, dedup, rec, index, answers, artifacts, start_t)
        write_json(result_path, result)
        append_checkpoint(args.work_dir, "1_mechanical",
                          "pipeline finished; files=%d" % len(inv["files"]),
                          time.time() - start_t)
        print("finalize: %d files, %d dup groups, %d version candidates, %d broken symlinks, "
              "%d mismatches -> %s" % (
                  len(inv["files"]), len(dedup["duplicate_groups"]),
                  len(dedup["version_candidates"]),
                  len(dedup["aliases"]["broken_symlinks"]),
                  len(rec.get("mismatches", [])), result_path))
        return 0
    except OSError as exc:
        # write failure or other OS-level problem: preserve skeleton, exit 2
        try:
            result = read_json(result_path)
        except Exception:
            contract_key, contract = discover_contract(case)
            if contract is None:
                contract = default_contract()
            result = build_skeleton(discover_case_id(case, task_text), "input_error", contract,
                                    task_title(case, task_text))
            write_json(result_path, result)
        result = result or {}
        result.setdefault("errors", []).append({"path": "<output>", "kind": "os_error", "detail": str(exc)})
        write_json(result_path, result)
        print("write_failure: %s" % exc)
        return 2


def build_parser():
    p = argparse.ArgumentParser(description="document library repair pipeline")
    p.add_argument("--case-json", default="/input/case.json")
    p.add_argument("--task-path", default="/input/task.md")
    p.add_argument("--input-root", default="/input")
    p.add_argument("--output-dir", default="/app/output")
    p.add_argument("--work-dir", default="/app/work")
    p.add_argument("--result", default=None, help="target result.json (for --update/--check)")
    p.add_argument("result_arg", nargs="?", default=None,
                   help="optional positional result.json path (for --check)")
    p.add_argument("--update", metavar="PATCH.json", default=None)
    p.add_argument("--check", action="store_true")
    p.add_argument("--skeleton-only", action="store_true")
    p.add_argument("--budget", action="store_true")
    p.add_argument("--max-hash", type=int, default=DEFAULT_MAX_HASH_BYTES)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return run_pipeline(args)


if __name__ == "__main__":
    sys.exit(main())
