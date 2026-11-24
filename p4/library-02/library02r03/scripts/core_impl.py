"""core_impl.py - deterministic scientific document library repair.

Pure standard library. Designed around a single hard requirement: running the
same input twice produces byte-identical artifacts except for explicitly
volatile fields (generated_at / runtime / time). All traversal is sorted, all
hashing is stable, JSON is serialized with sorted keys, and no randomness is
used anywhere.
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
import sys
import time
import zipfile
from datetime import datetime, timezone

VERSION = "1.1.0"

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_HASH_BYTES = 64 * 1024 * 1024      # do not hash regular files above 64 MiB
MAX_TEXT_BYTES = 4 * 1024 * 1024       # text preview / indexing cap
MAX_PARSE_BYTES = 16 * 1024 * 1024     # manifest / container parse cap
MAX_INDEX_TOKENS_PER_FILE = 2000
MAX_INDEX_ENTRIES = 20000
MAX_PREVIEW_CHARS = 1200

CONTRACT_KEYS = ("output_contract", "output", "result_schema", "contract",
                 "output_schema", "result")

RESERVED = {
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "runtime", "limitations",
    "computed", "generated_at", "task_title", "challenge_id", "task_path",
    "input_root", "output_dir", "work_dir", "results", "evidence",
    "failure_recovery", "output_contract",
}

LIST_APPEND_KEYS = {
    "sources", "uncertainty", "pending_human", "change_log", "errors",
    "degradations", "limitations",
}

TEXT_EXT = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl",
    ".yaml", ".yml", ".xml", ".html", ".htm", ".bib", ".ris", ".tex",
    ".py", ".sh", ".r", ".log", ".ini", ".cfg", ".toml", ".sql",
}

PDF_EXT = {".pdf"}
DOCX_EXT = {".docx"}
XLSX_EXT = {".xlsx", ".xlsm"}
PPTX_EXT = {".pptx"}
ZIP_EXT = {".zip", ".docx", ".xlsx", ".xlsm", ".pptx", ".odt", ".ods", ".odp"}

VERSION_RE = re.compile(
    r"[-_.\s]+(?:v|ver|version|rev|r|draft|final|copy|副本)?[-_.\s]*\d+(?:[-_.]\d+)*"
    r"|[-_.\s]*(?:copy|副本)(?:\s*\d+)?|[-_.\s]+(?:old|new|latest|final|draft)"
    r"|[-_.\s]*\(\d+\)|[-_.\s]*\[\d+\]$",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Small deterministic helpers
# --------------------------------------------------------------------------- #

def utcnow_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def json_dump(obj, path, indent=2):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, sort_keys=True, indent=indent)
        fh.write("\n")
    os.replace(tmp, path)


def json_load(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def read_text_fallback(path, cap=MAX_TEXT_BYTES):
    """Read text with encoding fallback. Returns (text, encoding_used, error)."""
    size = os.path.getsize(path)
    limit = min(size, cap)
    raw = b""
    with open(path, "rb") as fh:
        raw = fh.read(limit)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), enc, None
        except (UnicodeDecodeError, LookupError):
            continue
    try:
        return raw.decode("utf-8", errors="replace"), "utf-8(replace)", "bad_encoding"
    except Exception as exc:  # pragma: no cover - defensive
        return "", None, f"read_error:{exc}"


def normalize_stem(name):
    """Normalized file stem used to group version candidates."""
    stem, _ext = os.path.splitext(name)
    stem = VERSION_RE.sub("", stem)
    stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", stem.lower())
    if not stem:
        return "_no_stem_"
    # bare version-only stems ("v1", "ver2", "r3", "2") all collapse together
    if re.fullmatch(r"(?:v|ver|version|rev|r)?\d+(?:\.\d+)*", stem):
        return "_versioned_only_"
    return stem


def text_similarity(a, b):
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) > 200000 or len(b) > 200000:
        # cheap token Jaccard for very large texts
        ta, tb = set(a.split()), set(b.split())
        if not ta or not tb:
            return 0.0
        return round(len(ta & tb) / len(ta | tb), 6)
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 6)


def tokenize(text):
    out = {}
    for tok in re.findall(r"[0-9A-Za-z\u4e00-\u9fff]{2,}", text.lower()):
        out[tok] = out.get(tok, 0) + 1
    return out


def strip_xml(text):
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&(amp|lt|gt|quot|apos);", lambda m: {
        "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'",
    }[m.group(1)], text)
    return re.sub(r"\s+", " ", text).strip()


def _xml_stream_text(raw, tag, cap):
    """Extract text from the first zip member matching *tag*."""
    text = ""
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            if tag in name and name.endswith(".xml"):
                data = zf.read(name)
                if len(data) > cap:
                    data = data[:cap]
                text += strip_xml(data.decode("utf-8", errors="replace")) + " "
                if len(text) > cap:
                    break
    return text[:MAX_TEXT_BYTES]


def extract_container_text(path):
    """Best-effort text layer extraction for zip-backed Office files and PDFs.

    Returns (text, note). Never raises: any failure degrades to ''.
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        size = os.path.getsize(path)
        if size <= 0 or size > MAX_PARSE_BYTES:
            return "", f"container_too_large_or_empty:{size}"
        with open(path, "rb") as fh:
            raw = fh.read(MAX_PARSE_BYTES)
        if ext in DOCX_EXT:
            text = _xml_stream_text(raw, "word/document.xml", MAX_TEXT_BYTES)
            return text, ("office_text" if text else "no_text_layer")
        if ext in PPTX_EXT:
            text = _xml_stream_text(raw, "ppt/slides/slide", MAX_TEXT_BYTES)
            return text, ("office_text" if text else "no_text_layer")
        if ext in XLSX_EXT:
            shared = _xml_stream_text(raw, "xl/sharedStrings.xml", MAX_TEXT_BYTES)
            sheets = _xml_stream_text(raw, "xl/worksheets/sheet", MAX_TEXT_BYTES)
            text = (shared + " " + sheets).strip()
            return text, ("office_text" if text else "no_text_layer")
        if ext == ".pdf":
            return extract_pdf_text(raw)
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, KeyError,
            UnicodeDecodeError, ValueError) as exc:
        return "", f"container_error:{type(exc).__name__}"
    return "", ""


def extract_pdf_text(raw):
    """Naive best-effort PDF text extraction using only zlib (FlateDecode)."""
    parts = []
    try:
        for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.DOTALL):
            blob = m.group(1)
            if blob.startswith(b"\r"):
                blob = blob[1:]
            if blob.endswith(b"\n"):
                blob = blob[:-1]
            data = None
            try:
                data = zlib.decompress(blob)
            except zlib.error:
                data = blob  # maybe uncompressed
            if data:
                parts.append(data)
    except Exception:
        pass
    if not parts:
        return "", "no_text_layer"
    joined = b"\n".join(parts)
    # text-showing operators: (literal) Tj  and  [ (a) (b) ] TJ
    texts = []
    for m in re.finditer(rb"\(((?:[^()\\]|\\.)*)\)\s*Tj", joined):
        texts.append(_pdf_unescape(m.group(1)))
    for m in re.finditer(rb"\[((?:[^\[\]\\]|\\.)*)\]\s*TJ", joined):
        inner = m.group(1)
        for sm in re.finditer(rb"\(((?:[^()\\]|\\.)*)\)", inner):
            texts.append(_pdf_unescape(sm.group(1)))
    text = " ".join(t for t in texts if t).strip()
    if not text:
        return "", "no_text_layer"
    return text[:MAX_TEXT_BYTES], "pdf_text"


def _pdf_unescape(blob):
    s = blob.decode("latin-1", errors="replace")
    return s.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #

def discover_path(root, names, prefer_dir=False):
    """Find the first existing candidate under root (deterministic order)."""
    for name in names:
        p = os.path.join(root, name)
        if os.path.exists(p):
            return p
        p2 = os.path.join(root, *name.split("/")) if "/" in name else p
        if os.path.exists(p2):
            return p2
    return None


def discover_case_json(input_root):
    return discover_path(input_root, ("case.json", "input.json", "task.json"))


def discover_task_md(input_root):
    return discover_path(input_root, ("task.md", "README.md", "instructions.md"))


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #

def file_mode_str(st):
    try:
        return oct(st.st_mode & 0o7777)[2:]
    except Exception:
        return "?"


def inventory(input_root):
    """Deterministic recursive inventory of input_root.

    Returns dict(files=[...], dirs=[...], symlinks=[...], hardlink_groups=[...]).
    """
    files = []
    dirs = []
    symlinks = []
    inode_map = {}  # (dev, ino) -> list of rel paths

    root_abs = os.path.abspath(input_root)
    for dirpath, dirnames, filenames in os.walk(root_abs):
        dirnames.sort()
        filenames.sort()
        rel_dir = os.path.relpath(dirpath, root_abs)
        if rel_dir == ".":
            rel_dir = ""
        drec = {
            "path": rel_dir or ".",
            "type": "dir",
            "mode": None,
            "children": len(dirnames) + len(filenames),
        }
        try:
            drec["mode"] = file_mode_str(os.stat(dirpath))
        except OSError:
            drec["mode"] = "?"
        dirs.append(drec)
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root_abs).replace(os.sep, "/")
            rec = {
                "path": rel,
                "name": name,
                "ext": os.path.splitext(name)[1].lower(),
                "type": "file",
                "size": None,
                "mtime": None,
                "mode": None,
                "nlink": None,
                "is_symlink": False,
                "link_target": None,
                "hardlink_group": None,
                "sha256": None,
                "version_stem": normalize_stem(name),
                "degradation": None,
                "error": None,
                "text_layer": None,
                "text_preview": None,
            }
            try:
                lst = os.lstat(full)
                rec["mode"] = file_mode_str(lst)
                rec["nlink"] = int(getattr(lst, "st_nlink", 0) or 0)
                rec["mtime"] = int(getattr(lst, "st_mtime", 0) or 0)
            except OSError as exc:
                rec["error"] = f"stat_error:{type(exc).__name__}"
                files.append(rec)
                continue
            if os.path.islink(full):
                rec["type"] = "symlink"
                rec["is_symlink"] = True
                try:
                    rec["link_target"] = os.readlink(full)
                    if not os.path.exists(full):
                        rec["degradation"] = "broken_symlink"
                except OSError as exc:
                    rec["degradation"] = f"symlink_read_error:{type(exc).__name__}"
                symlinks.append(rec)
                files.append(rec)
                continue
            if not os.path.isfile(full):
                rec["type"] = "non_regular"
                rec["degradation"] = "non_regular"
                files.append(rec)
                continue
            rec["size"] = int(getattr(lst, "st_size", 0) or 0)
            # hash
            if rec["size"] > MAX_HASH_BYTES:
                rec["degradation"] = "too_large"
            else:
                h = hashlib.sha256()
                try:
                    with open(full, "rb") as fh:
                        for chunk in iter(lambda: fh.read(1 << 20), b""):
                            h.update(chunk)
                    rec["sha256"] = h.hexdigest()
                except OSError as exc:
                    rec["degradation"] = (rec["degradation"] or "unreadable")
                    rec["error"] = f"hash_error:{type(exc).__name__}"
            # hardlink bookkeeping
            st = None
            try:
                st = os.stat(full)
            except OSError:
                st = None
            if st is not None:
                key = (int(getattr(st, "st_dev", 0) or 0), int(getattr(st, "st_ino", 0) or 0))
                if key[1] != 0 and rec["nlink"] > 1:
                    inode_map.setdefault(key, []).append(rel)
            # text layer
            ext = rec["ext"]
            if ext in TEXT_EXT and rec["size"] <= MAX_TEXT_BYTES and rec["sha256"]:
                text, enc, err = read_text_fallback(full)
                rec["text_layer"] = enc or None
                rec["text_preview"] = text[:MAX_PREVIEW_CHARS] if text else None
                if err:
                    rec["degradation"] = (rec["degradation"] or err)
            elif ext in (PDF_EXT | DOCX_EXT | XLSX_EXT | PPTX_EXT) and rec["sha256"]:
                text, note = extract_container_text(full)
                rec["text_layer"] = note
                rec["text_preview"] = text[:MAX_PREVIEW_CHARS] if text else None
                if note and note not in ("office_text", "pdf_text"):
                    rec["degradation"] = (rec["degradation"] or note)
            elif ext in ZIP_EXT and rec["sha256"]:
                rec["text_layer"] = "container_not_extracted"
            files.append(rec)

    files.sort(key=lambda r: r["path"])
    dirs.sort(key=lambda r: r["path"])
    symlinks.sort(key=lambda r: r["path"])
    hardlink_groups = []
    for key in sorted(inode_map.keys(), key=lambda k: (k[0], k[1])):
        paths = sorted(set(inode_map[key]))
        if len(paths) > 1:
            hardlink_groups.append({"dev": key[0], "ino": key[1], "paths": paths})
    # mark empty dirs
    file_set = {r["path"] for r in files}
    for drec in dirs:
        drec["empty"] = drec["children"] == 0
    return {
        "files": files,
        "dirs": dirs,
        "symlinks": symlinks,
        "hardlink_groups": hardlink_groups,
    }


def _render_tree(node, prefix):
    lines = []
    keys = sorted(k for k in node.keys() if k != "__empty__")
    for i, k in enumerate(keys):
        last = i == len(keys) - 1
        child = node[k]
        branch = "`-- " if last else "|-- "
        if isinstance(child, dict):
            empty = child.get("__empty__", False)
            lines.append(prefix + branch + k + (" [空目录]" if empty else ""))
            lines.extend(_render_tree(child, prefix + ("    " if last else "|   ")))
        else:
            lines.append(prefix + branch + k)
    return lines


def directory_tree(files, dirs):
    """Render a deterministic directory tree string (files and dirs)."""
    tree = {}
    for d in dirs:
        if d["path"] == ".":
            continue
        node = tree
        for p in d["path"].replace("\\", "/").split("/"):
            node = node.setdefault(p, {})
        node["__empty__"] = d.get("empty", False)
    for f in files:
        node = tree
        parts = f["path"].replace("\\", "/").split("/")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = None  # leaf = file
    return "\n".join(["."] + _render_tree(tree, "")) + "\n"


# --------------------------------------------------------------------------- #
# Deduplication & version candidates
# --------------------------------------------------------------------------- #

def dedup(files):
    groups = {}
    for f in files:
        if f["type"] != "file" or not f["sha256"]:
            continue
        groups.setdefault(f["sha256"], []).append(f["path"])
    out = []
    for sha in sorted(groups.keys()):
        paths = sorted(groups[sha])
        if len(paths) > 1:
            out.append({"sha256": sha, "count": len(paths), "paths": paths})
    return out


def version_candidates(files, text_map):
    by_stem = {}
    for f in files:
        if f["type"] != "file" or not f["sha256"]:
            continue
        by_stem.setdefault(f["version_stem"], []).append(f)
    candidates = []
    for stem in sorted(by_stem.keys()):
        members = sorted(by_stem[stem], key=lambda r: (r["mtime"] or 0, r["path"]))
        if len(members) < 2:
            continue
        pairs = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                sim = text_similarity(
                    text_map.get(a["path"], "") or "",
                    text_map.get(b["path"], "") or "",
                )
                pairs.append({
                    "a": a["path"], "b": b["path"],
                    "similarity": sim,
                    "same_hash": a["sha256"] == b["sha256"],
                })
        pairs.sort(key=lambda p: (-p["similarity"], p["a"], p["b"]))
        candidates.append({
            "stem": stem,
            "members": [m["path"] for m in members],
            "pairs": pairs,
            "heuristic": True,
        })
    return candidates


# --------------------------------------------------------------------------- #
# Manifest parsing (JSON / CSV / Markdown / BibTeX / RIS / SQLite)
# --------------------------------------------------------------------------- #

def parse_manifest_file(path):
    """Parse a library manifest. Returns dict or list of records, else None."""
    ext = os.path.splitext(path)[1].lower()
    size = os.path.getsize(path)
    if size > MAX_PARSE_BYTES:
        return None
    try:
        if ext == ".json":
            return json_load(path)
        if ext == ".csv":
            return parse_csv(path)
        if ext in (".md", ".markdown"):
            text, _, _ = read_text_fallback(path)
            return parse_markdown(text)
        if ext == ".bib":
            return parse_bibtex(path)
        if ext == ".ris":
            return parse_ris(path)
        if ext in (".db", ".sqlite", ".sqlite3"):
            return parse_sqlite(path)
    except Exception:
        return None
    return None


def parse_csv(path):
    rows = []
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        for row in reader:
            rows.append(row)
    return rows


def parse_markdown(text):
    recs = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("|") and line.endswith("|") and "|" in line[1:-1]:
            cells = [c.strip() for c in line.strip("|").split("|")]
            recs.append(cells)
        elif ":" in line and not line.startswith("#"):
            k, v = line.split(":", 1)
            if k.strip() and v.strip():
                recs.append([k.strip(), v.strip()])
    return recs if recs else None


def parse_bibtex(path):
    text, _, _ = read_text_fallback(path)
    entries = []
    entry_re = re.compile(r"@(\w+)\s*\{([^,]+),(.*?)\}\s*\n?\s*(?=@|\Z)", re.DOTALL)
    for m in entry_re.finditer(text):
        kind, key, body = m.group(1), m.group(2).strip(), m.group(3)
        fields = {"type": kind, "key": key}
        for fm in re.finditer(r"(\w+)\s*=\s*[{\"]([^}\"]*)[}\"]", body):
            fields[fm.group(1).lower()] = fm.group(2)
        entries.append(fields)
    return entries if entries else None


def parse_ris(path):
    text, _, _ = read_text_fallback(path)
    entries, cur = [], None
    for line in text.splitlines():
        if line.startswith("TY  -"):
            if cur:
                entries.append(cur)
            cur = {"type": line[5:].strip()}
        elif line.startswith("ER  -"):
            if cur:
                entries.append(cur)
            cur = None
        elif line.startswith("  -") or "  -" in line:
            if cur is not None:
                tag, val = line[:2], line[6:].strip()
                cur[tag.strip()] = val
    if cur:
        entries.append(cur)
    return entries if entries else None


def parse_sqlite(path):
    out = {"file": path, "tables": []}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return {"file": path, "error": f"sqlite_open_error:{type(exc).__name__}"}
    try:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [r[0] for r in cur.fetchall()]
        for t in tables:
            try:
                cur.execute(f"SELECT * FROM {t} LIMIT 50")
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = [list(r) for r in cur.fetchall()]
                out["tables"].append({"name": t, "columns": cols, "rows": rows})
            except sqlite3.Error as exc:
                out["tables"].append({"name": t, "error": f"sqlite_query_error:{type(exc).__name__}"})
    except sqlite3.Error as exc:
        out["error"] = f"sqlite_query_error:{type(exc).__name__}"
    finally:
        con.close()
    return out


def flatten_manifest_records(data):
    """Turn parsed manifest data into a deterministic list of records."""
    recs = []
    if isinstance(data, list):
        recs = data
    elif isinstance(data, dict):
        for k in ("files", "entries", "items", "records", "documents"):
            if isinstance(data.get(k), list):
                recs.extend(data[k])
        if not recs:
            recs = [data]
    return [r for r in recs if isinstance(r, (dict, list))]


def record_path_fields(rec):
    """Extract candidate path/name/hash/version fields from a record."""
    if isinstance(rec, list):
        rec = {"c%d" % i: v for i, v in enumerate(rec)}
    out = {}
    for k, v in rec.items():
        kl = str(k).lower()
        if kl in ("path", "file", "filepath", "file_path", "filename", "name", "relpath"):
            out.setdefault("path", v)
        if kl in ("sha256", "sha", "hash", "checksum", "md5"):
            out.setdefault("hash", v)
        if kl in ("version", "ver", "rev"):
            out.setdefault("version", v)
        if kl in ("title", "id", "doi"):
            out.setdefault("title", v)
    return out


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #

def reconcile(files, manifests):
    """Compare manifest-declared entries against the actual inventory."""
    file_by_path = {f["path"]: f for f in files}
    file_by_name = {}
    for f in files:
        file_by_name.setdefault(f["name"].lower(), []).append(f["path"])

    declared = []      # normalized manifest entries
    manifest_meta = []
    for m in manifests:
        data = m.get("data")
        recs = flatten_manifest_records(data) if data is not None else []
        for r in recs:
            pf = record_path_fields(r)
            if pf.get("path") is None and pf.get("hash") is None:
                continue
            declared.append({"manifest": m.get("path"), "raw": r, **pf})
        manifest_meta.append({"path": m.get("path"), "kind": m.get("kind"),
                              "records": len(recs),
                              "error": (data or {}).get("error") if isinstance(data, dict) else None})

    matches, mismatches, missing, orphaned = [], [], [], []
    matched_paths = set()
    declared_paths = set()
    for d in declared:
        p = d.get("path")
        if isinstance(p, str):
            declared_paths.add(p)
        target = None
        if isinstance(p, str):
            target = file_by_path.get(p)
            if target is None and p in file_by_name:
                cands = file_by_name[p]
                target = file_by_path[cands[0]] if len(cands) == 1 else None
        rec = {"manifest": d["manifest"], "declared_path": p,
               "declared_hash": d.get("hash"), "declared_version": d.get("version")}
        if target is None:
            missing.append(rec)
            continue
        rec["actual_path"] = target["path"]
        rec["actual_hash"] = target["sha256"]
        if d.get("hash") and target["sha256"] and d["hash"] != target["sha256"]:
            mismatches.append(rec)
        else:
            matches.append(rec)
            matched_paths.add(target["path"])

    if declared_paths:
        for f in files:
            if f["type"] != "file":
                continue
            if f["path"] not in matched_paths and f["path"] not in declared_paths:
                orphaned.append({"path": f["path"], "sha256": f["sha256"]})

    return {
        "manifest_files": manifest_meta,
        "matches": sorted(matches, key=lambda r: (r["actual_path"] or "", r["manifest"])),
        "mismatches": sorted(mismatches, key=lambda r: (r["actual_path"] or "", r["manifest"])),
        "missing": sorted(missing, key=lambda r: (r["declared_path"] or "", r["manifest"])),
        "orphaned": sorted(orphaned, key=lambda r: r["path"]),
    }


# --------------------------------------------------------------------------- #
# Index & questions
# --------------------------------------------------------------------------- #

def build_index(files):
    """Deterministic token -> paths index with per-file token counts."""
    index = {}
    for f in files:
        text = f.get("text_preview") or ""
        if not text:
            continue
        for tok, _count in sorted(tokenize(text[:MAX_TEXT_BYTES]).items()):
            index.setdefault(tok, [])
            index[tok].append(f["path"])
    for tok in list(index.keys()):
        index[tok] = sorted(set(index[tok]))
    # cap total postings deterministically by dropping the rarest tokens first
    while sum(len(v) for v in index.values()) > MAX_INDEX_ENTRIES and index:
        rarest = min(index.keys(), key=lambda t: (len(index[t]), t))
        del index[rarest]
    return index


def extract_questions(case, task_text):
    """Extract retrieval questions from case.json and task.md (deterministic)."""
    qs = []
    if isinstance(case, dict):
        for k in ("questions", "queries", "questions_to_answer", "asks"):
            v = case.get(k)
            if isinstance(v, list):
                qs.extend([x for x in v if isinstance(x, (str, dict))])
            elif isinstance(v, str):
                qs.append(v)
    if task_text:
        for pat in (r"#+\s*(?:检索问题|提问|问题|Questions?|Queries)\s*\n(.*?)(?=\n#|\Z)",
                    r"(?:问题|提问|检索问题)\s*[:：]\s*(.+)"):
            for m in re.finditer(pat, task_text, re.DOTALL):
                block = m.group(1) if m.lastindex else m.group(0)
                for line in block.splitlines():
                    line = line.strip().lstrip("-*0123456789.、) ").strip()
                    if line and not line.startswith("#"):
                        qs.append(line)
    # dedupe keep order
    seen, out = set(), []
    for q in qs:
        key = json.dumps(q, ensure_ascii=False, sort_keys=True) if not isinstance(q, str) else q
        if key not in seen:
            seen.add(key)
            out.append(q)
    return out


def mechanical_answers(inv, dup, ver, recon, aliases, index):
    """Deterministic answers to mechanical questions."""
    files = inv["files"]
    reg_files = [f for f in files if f["type"] == "file"]
    total_size = sum(f["size"] or 0 for f in reg_files)
    broken = [s for s in inv["symlinks"] if s.get("degradation") == "broken_symlink"]
    deg_files = [f for f in files if f.get("degradation") or f.get("error")]
    ext_hist = {}
    for f in reg_files:
        ext = f["ext"] or "(none)"
        ext_hist[ext] = ext_hist.get(ext, 0) + 1
    answers = [
        {"question": "盘点统计（文件/目录/体积）",
         "answer": f"共 {len(reg_files)} 个常规文件、{len(inv['dirs'])} 个目录、"
                   f"{len(inv['symlinks'])} 个符号链接、{total_size} 字节；"
                   f"扩展名分布：{json.dumps(ext_hist, ensure_ascii=False, sort_keys=True)}",
         "evidence": [{"item": "inventory.summary",
                       "locator": "computed.summary",
                       "quote": json.dumps({"regular_files": len(reg_files),
                                            "dirs": len(inv['dirs']),
                                            "symlinks": len(inv['symlinks']),
                                            "total_size": total_size},
                                           ensure_ascii=False, sort_keys=True)}]},
        {"question": "重复文件组（按内容 SHA-256）",
         "answer": f"共 {len(dup)} 组重复文件。",
         "evidence": [{"item": "dedup", "locator": "computed.duplicate_groups",
                       "quote": json.dumps(dup[:5], ensure_ascii=False)}]},
        {"question": "断链符号链接",
         "answer": f"共 {len(broken)} 个断链：{[s['path'] for s in broken]}",
         "evidence": [{"item": "aliases", "locator": "computed.aliases.broken_symlinks",
                       "quote": json.dumps([s['path'] for s in broken], ensure_ascii=False)}]},
        {"question": "恢复对账（missing/mismatch/orphaned）",
         "answer": f"matches={len(recon['matches'])}, mismatches={len(recon['mismatches'])}, "
                   f"missing={len(recon['missing'])}, orphaned={len(recon['orphaned'])}",
         "evidence": [{"item": "reconciliation", "locator": "computed.reconciliation",
                       "quote": json.dumps({k: len(v) for k, v in recon.items()
                                            if k in ('matches', 'mismatches', 'missing', 'orphaned')},
                                           ensure_ascii=False)}]},
        {"question": "版本候选链（归一化主干 + 内容相似度，启发式）",
         "answer": f"共 {len(ver)} 组版本候选。",
         "evidence": [{"item": "version_candidates", "locator": "computed.version_candidates",
                       "quote": json.dumps([{"stem": c['stem'], "members": c['members']}
                                            for c in ver[:5]], ensure_ascii=False)}]},
        {"question": "难处理/降级文件",
         "answer": f"共 {len(deg_files)} 个文件带 degradation/error："
                   f"{[f['path'] for f in deg_files]}",
         "evidence": [{"item": "degradations", "locator": "computed.files",
                       "quote": json.dumps([f['path'] for f in deg_files], ensure_ascii=False)}]},
    ]
    return answers


# --------------------------------------------------------------------------- #
# Result assembly
# --------------------------------------------------------------------------- #

def build_summary(inv, dup, ver, recon, aliases):
    reg = [f for f in inv["files"] if f["type"] == "file"]
    return {
        "regular_files": len(reg),
        "dirs": len(inv["dirs"]),
        "symlinks": len(inv["symlinks"]),
        "broken_symlinks": len(aliases["broken_symlinks"]),
        "hardlink_groups": len(inv["hardlink_groups"]),
        "duplicate_groups": len(dup),
        "version_candidate_groups": len(ver),
        "reconcile_matches": len(recon["matches"]),
        "reconcile_mismatches": len(recon["mismatches"]),
        "reconcile_missing": len(recon["missing"]),
        "reconcile_orphaned": len(recon["orphaned"]),
        "total_bytes": sum(f["size"] or 0 for f in reg),
    }


def assemble_result(case_id, status, contract, inv, dup, ver, aliases, recon,
                    index, answers, artifacts, notes):
    result = {
        "case_id": case_id,
        "status": status,
        "generated_at": utcnow_iso(),
        "runtime": {"seconds": None, "version": VERSION},
        "task_title": None,
        "challenge_id": None,
        "task_path": None,
        "input_root": None,
        "output_dir": None,
        "work_dir": None,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "computed": {
            "files": inv["files"],
            "dirs": inv["dirs"],
            "summary": build_summary(inv, dup, ver, recon, aliases),
            "duplicate_groups": dup,
            "version_candidates": ver,
            "version_notes": notes,
            "aliases": aliases,
            "reconciliation": recon,
            "answers": answers,
            "index": index,
            "artifacts": artifacts,
        },
    }
    # backfill contract fields to top level
    if isinstance(contract, dict):
        result["output_contract"] = contract
        for k, v in contract.items():
            if k in RESERVED:
                result.setdefault("computed", {})["_contract_" + k] = v
            else:
                result[k] = v
    return result


# --------------------------------------------------------------------------- #
# Artifacts
# --------------------------------------------------------------------------- #

def write_artifacts(output_dir, inv, dup, ver, aliases, recon, index,
                    op_log_entries, version_notes, case_id):
    os.makedirs(output_dir, exist_ok=True)
    artifacts = {}
    files = inv["files"]
    regular_files = [f for f in files if f["type"] == "file"]

    # CSV manifest
    csv_path = os.path.join(output_dir, "library_manifest.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["path", "name", "type", "ext", "size", "mtime", "mode",
                    "nlink", "sha256", "is_symlink", "link_target",
                    "hardlink_group", "version_stem", "text_layer", "degradation", "error"])
        for f in files:
            w.writerow([f["path"], f["name"], f["type"], f["ext"], f["size"],
                        f["mtime"], f["mode"], f["nlink"], f["sha256"],
                        "true" if f["is_symlink"] else "false",
                        f["link_target"] or "", f["hardlink_group"] or "",
                        f["version_stem"], f["text_layer"] or "",
                        f["degradation"] or "", f["error"] or ""])
    artifacts["library_manifest.csv"] = "library_manifest.csv"

    # directory tree
    tree_path = os.path.join(output_dir, "directory_tree.txt")
    with open(tree_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(directory_tree(files, inv["dirs"]))
    artifacts["directory_tree.txt"] = "directory_tree.txt"

    # dedup + version + aliases
    dedup_path = os.path.join(output_dir, "dedup_version.json")
    json_dump({"duplicate_groups": dup, "version_candidates": ver,
               "version_notes": version_notes, "aliases": aliases}, dedup_path)
    artifacts["dedup_version.json"] = "dedup_version.json"

    # reconciliation
    rec_path = os.path.join(output_dir, "recovery_report.json")
    json_dump(recon, rec_path)
    artifacts["recovery_report.json"] = "recovery_report.json"

    # index
    idx_path = os.path.join(output_dir, "library_index.json")
    json_dump({"case_id": case_id, "index": index,
               "stats": {"tokens": len(index),
                         "postings": sum(len(v) for v in index.values())}}, idx_path)
    artifacts["library_index.json"] = "library_index.json"

    # handoff markdown
    md_path = os.path.join(output_dir, "library_manifest.md")
    with open(md_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"# 科研资料库交接报告\n\ncase_id: `{case_id}`\n\n")
        fh.write(f"## 统计\n\n- 常规文件：{len(regular_files)}\n")
        fh.write(f"- 目录：{len(inv['dirs'])}\n")
        fh.write(f"- 符号链接：{len(inv['symlinks'])}\n")
        fh.write(f"- 重复组：{len(dup)}\n- 版本候选组：{len(ver)}\n")
        fh.write(f"- 对账：matches={len(recon['matches'])}, mismatches={len(recon['mismatches'])}, "
                 f"missing={len(recon['missing'])}, orphaned={len(recon['orphaned'])}\n\n")
        fh.write("## 证据与复核\n\n所有哈希、路径、版本与别名记录见 `library_manifest.csv`、"
                 "`dedup_version.json`、`recovery_report.json`；可检索索引见 `library_index.json`。\n")
    artifacts["library_manifest.md"] = "library_manifest.md"

    # operation log
    log_path = os.path.join(output_dir, "operation_log.jsonl")
    with open(log_path, "w", encoding="utf-8", newline="\n") as fh:
        for e in op_log_entries:
            fh.write(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n")
    artifacts["operation_log.jsonl"] = "operation_log.jsonl"

    return artifacts


# --------------------------------------------------------------------------- #
# Version notes / administrative version parsing
# --------------------------------------------------------------------------- #

def extract_version_notes(task_text, input_root):
    """Extract administrative version notes from task.md and note-like files."""
    notes = []
    ts = utcnow_iso()
    if task_text:
        for pat in (r"#+\s*(?:版本说明|行政版本|Version notes?|Versions)\s*\n(.*?)(?=\n#|\Z)",
                    r"(?:版本说明|行政版本|version note)\s*[:：]\s*(.+)"):
            for m in re.finditer(pat, task_text, re.DOTALL | re.IGNORECASE):
                block = m.group(1) if m.lastindex else m.group(0)
                for line in block.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        notes.append({"source": "task.md", "quote": line,
                                      "locator": "task.md"})
    for name in ("version_notes.md", "versions.md", "VERSION", "CHANGELOG.md",
                 "version_notes.json", "versions.json"):
        p = discover_path(input_root, (name,))
        if p:
            try:
                text, _, _ = read_text_fallback(p)
                notes.append({"source": os.path.relpath(p, input_root).replace(os.sep, "/"),
                              "quote": text[:MAX_PREVIEW_CHARS],
                              "locator": os.path.relpath(p, input_root).replace(os.sep, "/")})
            except OSError:
                continue
    # de-dup deterministic
    seen, out = set(), []
    for n in notes:
        key = (n.get("source"), n.get("quote"))
        if key not in seen:
            seen.add(key)
            out.append(n)
    return out


# --------------------------------------------------------------------------- #
# Case id & contract discovery
# --------------------------------------------------------------------------- #

def identify_case(case, task_text):
    case_id = None
    challenge_id = None
    if isinstance(case, dict):
        case_id = case.get("case_id") or case.get("id") or case.get("caseId")
        challenge_id = case.get("challenge_id") or case.get("challengeId")
    if not case_id and task_text:
        m = re.search(r"case[_ -]?id\s*[:：]\s*[`'\"]?([A-Za-z0-9._-]+)", task_text, re.I)
        if m:
            case_id = m.group(1)
        m2 = re.search(r"challenge[_ -]?id\s*[:：]\s*[`'\"]?([A-Za-z0-9._-]+)", task_text, re.I)
        if m2 and not challenge_id:
            challenge_id = m2.group(1)
    if not case_id and challenge_id:
        case_id = challenge_id
    if not case_id:
        case_id = "unknown-case"
    return case_id, challenge_id


def extract_contract(case):
    if not isinstance(case, dict):
        return None
    for k in CONTRACT_KEYS:
        if isinstance(case.get(k), dict):
            return case[k]
        if isinstance(case.get(k), str):
            try:
                parsed = json.loads(case[k])
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, TypeError):
                continue
    # nested under 'task'
    t = case.get("task")
    if isinstance(t, dict):
        for k in CONTRACT_KEYS:
            if isinstance(t.get(k), dict):
                return t[k]
    return None


# --------------------------------------------------------------------------- #
# Main pipeline
# --------------------------------------------------------------------------- #

def run_pipeline(args):
    input_root = args.input or DEFAULT_INPUT
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    case_json = args.case_json or discover_case_json(input_root)
    task_path = args.task_path or discover_task_md(input_root)

    case = {}
    task_text = ""
    if case_json:
        try:
            case = json_load(case_json) if isinstance(case_json, str) else case_json
        except (ValueError, OSError) as exc:
            case = {}
    if task_path:
        try:
            task_text, _, _ = read_text_fallback(task_path, cap=MAX_TEXT_BYTES)
        except OSError:
            task_text = ""

    case_id, challenge_id = identify_case(case, task_text)
    contract = extract_contract(case)

    if not os.path.isdir(input_root) or not os.listdir(input_root):
        result = assemble_result(case_id, "insufficient_evidence", contract,
                                 {"files": [], "dirs": [], "symlinks": [],
                                  "hardlink_groups": []},
                                 [], [], {"broken_symlinks": [], "symlinks": []},
                                 {"manifest_files": [], "matches": [], "mismatches": [],
                                  "missing": [], "orphaned": []},
                                 {}, [], {}, [])
        result["task_title"] = None
        result["challenge_id"] = challenge_id
        result["task_path"] = task_path
        result["input_root"] = input_root
        result["output_dir"] = output_dir
        result["work_dir"] = work_dir
        result["errors"].append("empty_or_missing_input_directory")
        result["uncertainty"].append({"item": "input", "reason": "empty_or_missing_input",
                                      "severity": "high"})
        result["runtime"]["seconds"] = round(time.time(), 3)
        json_dump(result, os.path.join(output_dir, "result.json"))
        return result

    # 1. inventory
    inv = inventory(input_root)
    # 2. dedup
    dup = dedup(inv["files"])
    # text map for similarity
    text_map = {f["path"]: (f.get("text_preview") or "") for f in inv["files"]}
    # 3. version candidates
    ver = version_candidates(inv["files"], text_map)
    # aliases
    aliases = {
        "symlinks": [s["path"] for s in inv["symlinks"]],
        "broken_symlinks": [s["path"] for s in inv["symlinks"]
                            if s.get("degradation") == "broken_symlink"],
        "hardlink_groups": inv["hardlink_groups"],
    }
    # 4. manifests
    manifests = []
    for f in inv["files"]:
        low = f["name"].lower()
        if any(k in low for k in ("manifest", "library", "catalog", "index", "inventory")) \
                and f["ext"] in (".json", ".csv", ".md", ".markdown", ".bib", ".ris",
                                 ".db", ".sqlite", ".sqlite3"):
            data = parse_manifest_file(os.path.join(input_root, f["path"]))
            manifests.append({"path": f["path"], "kind": f["ext"], "data": data})
    # 5. reconciliation
    recon = reconcile(inv["files"], manifests)
    # 6. index
    index = build_index(inv["files"])
    # 7. questions
    questions = extract_questions(case, task_text)
    answers = mechanical_answers(inv, dup, ver, recon, aliases, index)
    if questions:
        answers.append({"question": "案例显式提问（待语义确认）",
                        "answer": "脚本已给出机械候选；语义层答案由 Agent 用 --update 补充。",
                        "status": "pending_human",
                        "questions": questions})
    # 8. version notes
    notes = extract_version_notes(task_text, input_root)
    # change log (deterministic structure; only the shared `time` is volatile)
    start_ts = utcnow_iso()
    change_log = [
        {"seq": 1, "actor": "skill", "step": "skeleton", "detail": "partial skeleton written"},
        {"seq": 2, "actor": "skill", "step": "inventory", "detail": f"{len(inv['files'])} entries inventoried"},
        {"seq": 3, "actor": "skill", "step": "dedup", "detail": f"{len(dup)} duplicate groups"},
        {"seq": 4, "actor": "skill", "step": "version", "detail": f"{len(ver)} version candidate groups"},
        {"seq": 5, "actor": "skill", "step": "reconcile",
         "detail": f"matches={len(recon['matches'])} mismatches={len(recon['mismatches'])} "
                   f"missing={len(recon['missing'])} orphaned={len(recon['orphaned'])}"},
        {"seq": 6, "actor": "skill", "step": "index", "detail": f"{len(index)} index tokens"},
    ]
    for e in change_log:
        e["time"] = start_ts
    change_log.append({"seq": 7, "actor": "skill", "step": "artifacts",
                       "detail": "editable artifacts written", "time": start_ts})
    # 9. artifacts (operation log mirrors the full change_log)
    artifacts = write_artifacts(output_dir, inv, dup, ver, aliases, recon, index,
                                change_log, notes, case_id)

    result = assemble_result(case_id, "partial", contract, inv, dup, ver, aliases,
                             recon, index, answers, artifacts, notes)
    result["task_title"] = case.get("title") if isinstance(case, dict) else None
    result["challenge_id"] = challenge_id
    result["task_path"] = task_path
    result["input_root"] = input_root
    result["output_dir"] = output_dir
    result["work_dir"] = work_dir
    result["change_log"] = change_log
    result["errors"] = [f["error"] for f in inv["files"] if f.get("error")]
    result["degradations"] = [
        {"path": f["path"], "reason": f["degradation"]}
        for f in inv["files"] if f.get("degradation")
    ]
    if contract is None:
        result["uncertainty"].append({"item": "output_contract", "reason": "output_contract_missing",
                                      "severity": "low"})
    result["runtime"]["seconds"] = round(time.time(), 3)
    json_dump(result, os.path.join(output_dir, "result.json"))
    # also keep a copy in work dir for --update without --result
    json_dump(result, os.path.join(work_dir, "result.json"))
    return result


def skeleton(args):
    input_root = args.input or DEFAULT_INPUT
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    case_json = args.case_json or discover_case_json(input_root)
    task_path = args.task_path or discover_task_md(input_root)
    case = {}
    task_text = ""
    if case_json:
        try:
            case = json_load(case_json)
        except (ValueError, OSError):
            case = {}
    if task_path:
        try:
            task_text, _, _ = read_text_fallback(task_path, cap=MAX_TEXT_BYTES)
        except OSError:
            task_text = ""
    case_id, challenge_id = identify_case(case, task_text)
    contract = extract_contract(case)
    empty_inv = {"files": [], "dirs": [], "symlinks": [], "hardlink_groups": []}
    empty_recon = {"manifest_files": [], "matches": [], "mismatches": [], "missing": [], "orphaned": []}
    result = assemble_result(case_id, "partial", contract, empty_inv, [], [], 
                             {"broken_symlinks": [], "symlinks": []}, empty_recon,
                             {}, [], {}, [])
    result["task_title"] = case.get("title") if isinstance(case, dict) else None
    result["challenge_id"] = challenge_id
    result["task_path"] = task_path
    result["input_root"] = input_root
    result["output_dir"] = output_dir
    result["work_dir"] = work_dir
    result["runtime"]["seconds"] = round(time.time(), 3)
    result["change_log"] = [{"seq": 1, "actor": "skill", "step": "skeleton",
                             "detail": "partial skeleton written", "time": utcnow_iso()}]
    if contract is None:
        result["uncertainty"].append({"item": "output_contract",
                                      "reason": "output_contract_missing", "severity": "low"})
    json_dump(result, os.path.join(output_dir, "result.json"))
    json_dump(result, os.path.join(work_dir, "result.json"))
    return result


# --------------------------------------------------------------------------- #
# --update deep merge
# --------------------------------------------------------------------------- #

def deep_merge(base, patch):
    for k, v in patch.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        elif k in base and isinstance(base[k], list) and isinstance(v, list) \
                and k in LIST_APPEND_KEYS:
            base[k] = base[k] + v
        else:
            base[k] = v
    return base


def update(args):
    result_path = args.result
    if not result_path:
        result_path = os.path.join(args.work or DEFAULT_WORK, "result.json")
    if not os.path.exists(result_path):
        print(f"error: result.json not found at {result_path}", file=sys.stderr)
        return 2
    base = json_load(result_path)
    patch = json_load(args.update)
    merged = deep_merge(base, patch)
    json_dump(merged, result_path)
    # also mirror to output dir if different
    out_path = os.path.join(args.output or DEFAULT_OUTPUT, "result.json")
    if os.path.abspath(out_path) != os.path.abspath(result_path):
        json_dump(merged, out_path)
    print(f"update: merged {args.update} into {result_path}")
    return 0


# --------------------------------------------------------------------------- #
# --check
# --------------------------------------------------------------------------- #

def check(args):
    path = args.check
    if not os.path.exists(path):
        print(f"FAIL: result.json not found: {path}", file=sys.stderr)
        return 1
    try:
        r = json_load(path)
    except (ValueError, OSError) as exc:
        print(f"FAIL: result.json not valid JSON: {exc}", file=sys.stderr)
        return 1
    problems = []
    if not r.get("case_id"):
        problems.append("case_id empty")
    if r.get("status") not in ("ok", "partial", "insufficient_evidence", "input_error"):
        problems.append(f"invalid status: {r.get('status')!r}")
    for req in ("sources", "uncertainty", "change_log"):
        if req not in r:
            problems.append(f"missing field: {req}")
    # contract fields present?
    if isinstance(r.get("output_contract"), dict):
        for k in r["output_contract"]:
            if k not in r and f"_contract_{k}" not in r.get("computed", {}):
                problems.append(f"contract field missing: {k}")
    # sources have locator + quote
    for i, s in enumerate(r.get("sources", [])):
        if not isinstance(s, dict) or not s.get("locator") or not s.get("quote"):
            problems.append(f"sources[{i}] lacks locator/quote")
    # artifacts exist
    artifacts = r.get("computed", {}).get("artifacts", {})
    out_dir = r.get("output_dir") or DEFAULT_OUTPUT
    for name, rel in artifacts.items():
        p = os.path.join(out_dir, rel) if not os.path.isabs(rel) else rel
        if not os.path.exists(p):
            problems.append(f"artifact missing: {rel}")
    if problems:
        for p in problems:
            print(f"FAIL: {p}", file=sys.stderr)
        return 1
    print(f"check OK: {path} (case_id={r.get('case_id')}, status={r.get('status')})")
    return 0


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #

def build_parser():
    p = argparse.ArgumentParser(prog="main.py", add_help=False)
    p.add_argument("--case-json")
    p.add_argument("--task-path")
    p.add_argument("--input")
    p.add_argument("--output")
    p.add_argument("--work")
    p.add_argument("--skeleton-only", action="store_true")
    p.add_argument("--update")
    p.add_argument("--result")
    p.add_argument("--check")
    p.add_argument("--help", "-h", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.help:
        build_parser().print_help()
        return 0
    try:
        if args.check:
            return check(args)
        if args.update:
            return update(args)
        if args.skeleton_only:
            skeleton(args)
            print("skeleton: wrote partial result.json")
            return 0
        result = run_pipeline(args)
        summary = result["computed"]["summary"]
        print(f"finalize: {summary['regular_files']} files, {summary['duplicate_groups']} dup groups, "
              f"{summary['version_candidate_groups']} version candidates, "
              f"{summary['broken_symlinks']} broken symlinks, "
              f"{summary['reconcile_mismatches']} reconcile mismatches -> "
              f"{os.path.join(result['output_dir'], 'result.json')}")
        return 0
    except OSError as exc:
        print(f"error (OSError): {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # never crash the harness with a traceback
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
